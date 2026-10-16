#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雙影片同步對比播放器 (Dual Video Synchronized Player)
- 左右兩支影片「0 距離」無縫緊密貼合，無任何縫隙與黑邊干擾對比
- 各自下方均有獨立的起始時間點輸入欄位（支援 mm:ss.sss、純秒數）
- 影片中央下方提供同步「開始播放」、「暫停」、「回到起始點」與進度條控制
- 支援檔案拖曳、微調時間點（±0.1s、±1s）、單格步進、倍速播放、各自獨立靜音
"""

import sys
import os
import glob
from PyQt6.QtCore import Qt, QUrl, QTimer, QSize, QEvent, QPropertyAnimation
from PyQt6.QtGui import QIcon, QFont, QKeySequence, QShortcut, QColor, QPalette, QCursor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QPushButton, QLabel, QLineEdit, QSlider, QFileDialog,
    QFrame, QComboBox, QCheckBox, QMessageBox, QSizePolicy, QGraphicsOpacityEffect
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


def parse_time_to_ms(text: str) -> int:
    """將文字時間 (例如 '12.5', '01:23', '00:01:23.500') 轉為毫秒 (ms)"""
    text = text.strip()
    if not text:
        return 0
    parts = text.split(':')
    try:
        if len(parts) == 1:
            return max(0, int(float(parts[0]) * 1000))
        elif len(parts) == 2:
            m = float(parts[0])
            s = float(parts[1])
            return max(0, int((m * 60 + s) * 1000))
        elif len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return max(0, int((h * 3600 + m * 60 + s) * 1000))
    except (ValueError, TypeError):
        return 0
    return 0


def format_ms_to_time(ms: int, show_hours: bool = False) -> str:
    """將毫秒格式化為 mm:ss.zzz 或 hh:mm:ss.zzz"""
    if ms < 0:
        ms = 0
    total_sec = ms / 1000.0
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)
    s = total_sec % 60
    if show_hours or h > 0:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


class VideoDropZone(QVideoWidget):
    """支援檔案拖曳的視訊播放視窗 (0邊框無圓角，緊貼縫合，支援雙擊全螢幕)"""
    def __init__(self, parent_channel, parent=None):
        super().__init__(parent)
        self.channel = parent_channel
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000; border: none; margin: 0px; padding: 0px;")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.channel.main_window.toggle_fullscreen()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm')):
                self.channel.load_video(file_path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class VideoChannel:
    """管理單一影片的播放器邏輯與控制器"""
    def __init__(self, title: str, channel_id: int, main_window):
        self.channel_id = channel_id
        self.main_window = main_window
        self.title_text = title
        self.file_path = ""
        self.start_offset_ms = 0
        self.duration_ms = 0
        self.aspect_ratio = None  # width / height

        # 初始化播放器與音訊輸出
        self.player = QMediaPlayer(main_window)
        self.audio_output = QAudioOutput(main_window)
        self.player.setAudioOutput(self.audio_output)

        # 視訊畫面元件
        self.video_widget = VideoDropZone(self)
        self.player.setVideoOutput(self.video_widget)

        # 監聽視訊訊號
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        if self.video_widget.videoSink():
            self.video_widget.videoSink().videoSizeChanged.connect(self.on_video_size_changed)

    def on_video_size_changed(self):
        sz = self.video_widget.videoSink().videoSize()
        if sz.width() > 0 and sz.height() > 0:
            self.aspect_ratio = sz.width() / sz.height()
            self.main_window.video_stage.set_video_aspect(self.channel_id, self.aspect_ratio)

    def select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.main_window, f"選擇 {self.title_text}", "", "影片檔案 (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm);;所有檔案 (*.*)"
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path: str):
        if not os.path.exists(file_path):
            return
        self.file_path = file_path
        filename = os.path.basename(file_path)
        self.lbl_filename.setText(filename)
        self.lbl_filename.setToolTip(file_path)

        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.pause()
        self.apply_start_time()
        self.main_window.on_video_loaded(self.channel_id)

    def on_duration_changed(self, duration: int):
        self.duration_ms = duration
        self.update_time_label()
        self.main_window.recalculate_timeline()

    def on_position_changed(self, position: int):
        self.update_time_label()

    def update_time_label(self):
        curr_text = format_ms_to_time(self.player.position())
        dur_text = format_ms_to_time(self.duration_ms)
        self.lbl_time_status.setText(f"當前時間: {curr_text} / {dur_text}")

    def apply_start_time(self):
        raw_text = self.edit_start_time.text()
        ms = parse_time_to_ms(raw_text)
        if self.duration_ms > 0 and ms > self.duration_ms:
            ms = self.duration_ms
        self.start_offset_ms = ms
        self.edit_start_time.setText(format_ms_to_time(ms))
        self.player.setPosition(ms)
        self.main_window.recalculate_timeline()

    def set_current_as_start(self):
        curr_pos = self.player.position()
        self.start_offset_ms = curr_pos
        self.edit_start_time.setText(format_ms_to_time(curr_pos))
        self.main_window.recalculate_timeline()

    def step_start_time(self, delta_ms: int):
        new_ms = max(0, self.start_offset_ms + delta_ms)
        if self.duration_ms > 0 and new_ms > self.duration_ms:
            new_ms = self.duration_ms
        self.start_offset_ms = new_ms
        self.edit_start_time.setText(format_ms_to_time(new_ms))
        self.player.setPosition(new_ms)
        self.main_window.recalculate_timeline()

    def toggle_mute(self, checked: bool):
        self.audio_output.setMuted(checked)


class SeamlessVideoStage(QWidget):
    """兩支影片緊密相鄰的『0 距離』無縫舞台元件"""
    def __init__(self, channel_left: VideoChannel, channel_right: VideoChannel, parent=None):
        super().__init__(parent)
        self.left_channel = channel_left
        self.right_channel = channel_right
        self.aspect1 = None
        self.aspect2 = None
        self.zero_gap_fit = True
        self.fill_screen_mode = False  # False: 完整等比 (Fit 邊界), True: 畫面填滿最大化 (Fill/Zoom 滿版)
        self.show_divider = False

        self.setStyleSheet("background-color: #05070c; border: 1px solid #334155; border-radius: 6px;")
        self.setMouseTracking(True)

        # 兩支影片的 QVideoWidget
        self.left_widget = channel_left.video_widget
        self.right_widget = channel_right.video_widget
        self.left_widget.setParent(self)
        self.right_widget.setParent(self)
        # 可選的 1px 細微中線 (預設關閉為真正 0 距離)
        self.divider = QFrame(self)
        self.divider.setFixedWidth(1)
        self.divider.setStyleSheet("background-color: #38bdf8;")
        self.divider.setVisible(False)

        # 提示標籤 (無影片時顯示)
        self.hint_label = QLabel("可直接將左、右兩支影片拖曳進來對比\n兩影片將在正中央 0 距離無縫貼合", self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet("color: #475569; font-size: 15px; font-weight: bold; background: transparent;")
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.left_channel.main_window.toggle_fullscreen()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def set_video_aspect(self, channel_id: int, aspect: float):
        if channel_id == 1:
            self.aspect1 = aspect
        else:
            self.aspect2 = aspect
        self.hint_label.setVisible(False)
        self.update_layout()

    def set_zero_gap_mode(self, enabled: bool):
        self.zero_gap_fit = enabled
        self.update_layout()

    def set_fill_screen_mode(self, enabled: bool):
        self.fill_screen_mode = enabled
        self.update_layout()

    def set_divider_visible(self, visible: bool):
        self.show_divider = visible
        self.update_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_layout()

    def update_layout(self):
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        self.hint_label.setGeometry(0, 0, w, h)

        # 當雙方皆有解析度比例且開啟 0 距離貼合時：精確消除兩邊黑邊，使影片邊緣在中央 0 像素無縫相接！
        if self.zero_gap_fit and self.aspect1 and self.aspect2:
            r1 = self.aspect1
            r2 = self.aspect2
            sum_r = r1 + r2

            # 計算滿版放大 vs 等比適配
            if self.fill_screen_mode:
                # 滿版最大化 (Fill Mode)：取較大縮放比例，填滿螢幕不留任何黑邊
                if w / sum_r >= h:
                    ideal_h = w / sum_r
                else:
                    ideal_h = h
            else:
                # 完整等比放大 (Fit Mode)：取最大可能大小且完全容納於舞台中
                ideal_h = h
                if ideal_h * sum_r > w:
                    ideal_h = w / sum_r

            w1 = int(round(ideal_h * r1))
            w2 = int(round(ideal_h * r2))
            final_h = int(round(ideal_h))

            # 將整個相連的影片區塊置中
            total_w = w1 + w2
            start_x = (w - total_w) // 2
            start_y = (h - final_h) // 2

            # 左影片右邊緣恰好在 start_x + w1，右影片左邊緣恰好在 start_x + w1 (間距 = 0)
            self.left_widget.setGeometry(start_x, start_y, w1, final_h)
            self.right_widget.setGeometry(start_x + w1, start_y, w2, final_h)

            if self.show_divider:
                self.divider.setGeometry(start_x + w1, start_y, 1, final_h)
                self.divider.setVisible(True)
                self.divider.raise_()
            else:
                self.divider.setVisible(False)
        else:
            # 均分各 50% 寬度且中間 0 間隙
            mid = w // 2
            self.left_widget.setGeometry(0, 0, mid, h)
            self.right_widget.setGeometry(mid, 0, w - mid, h)

            if self.show_divider:
                self.divider.setGeometry(mid, 0, 1, h)
                self.divider.setVisible(True)
                self.divider.raise_()
            else:
                self.divider.setVisible(False)


class DualVideoPlayerWindow(QMainWindow):
    """主視窗：左右兩支影片 0 距離並排，各自下方有起始時間輸入，中央下方集中控制"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("雙影片同步對比播放器 (0 距離無縫對齊)")
        self.resize(1320, 840)
        self.setMinimumSize(980, 620)

        # 狀態變數
        self.is_playing = False
        self.is_user_scrubbing = False
        self.sync_duration_ms = 0
        self.relative_pos_ms = 0
        self.is_fullscreen_mode = False

        self.apply_dark_theme()
        self.init_channels()
        self.init_ui()
        self.setup_sync_timer()
        self.setup_fullscreen_helpers()
        self.setup_shortcuts()
        self.try_auto_load_local_videos()

    def apply_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#0b0f19"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#1e293b"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#1e293b"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f8fafc"))
        self.setPalette(palette)
        self.setStyleSheet("""
            QMainWindow { background-color: #0b0f19; }
            QLabel { color: #f8fafc; }
            QSlider::groove:horizontal {
                height: 8px;
                background: #334155;
                border-radius: 4px;
            }
            QSlider::sub-page:horizontal {
                background: #0284c7;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #38bdf8;
                width: 18px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: #7dd3fc;
            }
        """)

    def init_channels(self):
        self.channel_left = VideoChannel("影片 1 (左側)", 1, self)
        self.channel_right = VideoChannel("影片 2 (右側)", 2, self)

    def init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(10, 8, 10, 8)
        self.main_layout.setSpacing(6)

        # ========================================================
        # 1. 頂部資訊列：左影片資訊 與 右影片資訊 並列
        # ========================================================
        self.top_bar_widget = QWidget()
        top_bar_layout = QHBoxLayout(self.top_bar_widget)
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(10)

        # (左側頂部列)
        left_top = QHBoxLayout()
        lbl_left_title = QLabel("<b>影片 1 (左側)</b>")
        lbl_left_title.setStyleSheet("font-size: 14px; color: #38bdf8;")
        left_top.addWidget(lbl_left_title)

        btn_select_1 = QPushButton("📂 選擇影片")
        btn_select_1.setStyleSheet(
            "QPushButton { background-color: #1e293b; color: #e2e8f0; border: 1px solid #475569; "
            "border-radius: 4px; padding: 4px 10px; font-weight: bold; font-size: 12px; } "
            "QPushButton:hover { background-color: #334155; border-color: #38bdf8; }"
        )
        btn_select_1.clicked.connect(self.channel_left.select_file)
        left_top.addWidget(btn_select_1)

        self.channel_left.lbl_filename = QLabel("未載入影片 (支援拖曳)")
        self.channel_left.lbl_filename.setStyleSheet("color: #94a3b8; font-size: 12px;")
        left_top.addWidget(self.channel_left.lbl_filename, 1)

        self.channel_left.chk_mute = QCheckBox("靜音")
        self.channel_left.chk_mute.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        self.channel_left.chk_mute.toggled.connect(self.channel_left.toggle_mute)
        left_top.addWidget(self.channel_left.chk_mute)

        top_bar_layout.addLayout(left_top, 1)

        # 頂部中央分隔小標記
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #334155;")
        top_bar_layout.addWidget(sep)

        # (右側頂部列)
        right_top = QHBoxLayout()
        lbl_right_title = QLabel("<b>影片 2 (右側)</b>")
        lbl_right_title.setStyleSheet("font-size: 14px; color: #38bdf8;")
        right_top.addWidget(lbl_right_title)

        btn_select_2 = QPushButton("📂 選擇影片")
        btn_select_2.setStyleSheet(
            "QPushButton { background-color: #1e293b; color: #e2e8f0; border: 1px solid #475569; "
            "border-radius: 4px; padding: 4px 10px; font-weight: bold; font-size: 12px; } "
            "QPushButton:hover { background-color: #334155; border-color: #38bdf8; }"
        )
        btn_select_2.clicked.connect(self.channel_right.select_file)
        right_top.addWidget(btn_select_2)

        self.channel_right.lbl_filename = QLabel("未載入影片 (支援拖曳)")
        self.channel_right.lbl_filename.setStyleSheet("color: #94a3b8; font-size: 12px;")
        right_top.addWidget(self.channel_right.lbl_filename, 1)

        self.channel_right.chk_mute = QCheckBox("靜音")
        self.channel_right.chk_mute.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        self.channel_right.chk_mute.toggled.connect(self.channel_right.toggle_mute)
        right_top.addWidget(self.channel_right.chk_mute)

        top_bar_layout.addLayout(right_top, 1)
        self.main_layout.addWidget(self.top_bar_widget)

        # ========================================================
        # 2. 視訊舞台區域：★ 兩支影片 0 距離無縫緊貼 ★
        # ========================================================
        self.video_stage = SeamlessVideoStage(self.channel_left, self.channel_right, self)
        self.video_stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_layout.addWidget(self.video_stage, 1)

        # ========================================================
        # 3. 底部完整控制容器 (包含左右時間控制與中央總控制列)
        # ========================================================
        self.bottom_control_panel = QFrame(self)
        self.bottom_control_panel.setObjectName("bottomControlPanel")
        self.bottom_control_panel.setStyleSheet("QFrame#bottomControlPanel { background: transparent; border: none; }")
        bottom_panel_layout = QVBoxLayout(self.bottom_control_panel)
        bottom_panel_layout.setContentsMargins(0, 0, 0, 0)
        bottom_panel_layout.setSpacing(6)

        # 兩支影片各自下方的控制列（時間顯示與起始時間點輸入）
        bottom_channels_layout = QHBoxLayout()
        bottom_channels_layout.setSpacing(10)

        # 左影片下方控制區塊
        left_ctrl_frame = self.create_channel_ctrl_frame(self.channel_left)
        bottom_channels_layout.addWidget(left_ctrl_frame, 1)

        # 右影片下方控制區塊
        right_ctrl_frame = self.create_channel_ctrl_frame(self.channel_right)
        bottom_channels_layout.addWidget(right_ctrl_frame, 1)

        bottom_panel_layout.addLayout(bottom_channels_layout)

        # ========================================================
        # 4. 影片中央下方總控制面板（播放、暫停、進度條、倍速等）
        # ========================================================
        center_control_frame = QFrame()
        center_control_frame.setStyleSheet(
            "QFrame { background-color: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 6px; }"
        )
        ctrl_layout = QVBoxLayout(center_control_frame)
        ctrl_layout.setContentsMargins(12, 6, 12, 6)
        ctrl_layout.setSpacing(6)

        # (A) 同步進度軸與時間顯示
        slider_layout = QHBoxLayout()
        self.lbl_sync_current = QLabel("00:00.000")
        self.lbl_sync_current.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #38bdf8; font-weight: bold;")
        slider_layout.addWidget(self.lbl_sync_current)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.setValue(0)
        self.timeline_slider.sliderPressed.connect(self.on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self.on_slider_released)
        self.timeline_slider.sliderMoved.connect(self.on_slider_moved)
        slider_layout.addWidget(self.timeline_slider, 1)

        self.lbl_sync_total = QLabel("00:00.000")
        self.lbl_sync_total.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #94a3b8;")
        slider_layout.addWidget(self.lbl_sync_total)
        ctrl_layout.addLayout(slider_layout)

        # (B) 中央控制按鈕群（開始播放、暫停置於中央正下方）
        center_btns_layout = QHBoxLayout()
        center_btns_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_btns_layout.setSpacing(10)

        # 回到起始點
        self.btn_reset = QPushButton("⏮ 回到起始點")
        self.btn_reset.setToolTip("將兩支影片同時重設至各自輸入的起始時間點")
        self.btn_reset.setStyleSheet(
            "QPushButton { background-color: #334155; color: #f8fafc; border: 1px solid #475569; "
            "border-radius: 5px; padding: 7px 14px; font-size: 13px; font-weight: bold; } "
            "QPushButton:hover { background-color: #475569; }"
        )
        self.btn_reset.clicked.connect(self.reset_to_start)
        center_btns_layout.addWidget(self.btn_reset)

        # 單格後退
        self.btn_step_back = QPushButton("◀ 上一格")
        self.btn_step_back.setToolTip("兩支影片同步後退約一幀 (33ms)")
        self.btn_step_back.setStyleSheet(
            "QPushButton { background-color: #334155; color: #cbd5e1; border: 1px solid #475569; "
            "border-radius: 5px; padding: 7px 10px; font-size: 12px; } "
            "QPushButton:hover { background-color: #475569; }"
        )
        self.btn_step_back.clicked.connect(lambda: self.step_sync(-33))
        center_btns_layout.addWidget(self.btn_step_back)

        # ★ 開始播放按鈕 (影片中央下方)
        self.btn_play = QPushButton("▶ 開始播放")
        self.btn_play.setToolTip("兩支影片同步開始播放 (空白鍵 Space)")
        self.btn_play.setFixedWidth(135)
        self.btn_play.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; border: none; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; } "
            "QPushButton:hover { background-color: #15803d; } "
            "QPushButton:pressed { background-color: #166534; }"
        )
        self.btn_play.clicked.connect(self.play_sync)
        center_btns_layout.addWidget(self.btn_play)

        # ★ 暫停按鈕 (影片中央下方)
        self.btn_pause = QPushButton("⏸ 暫停")
        self.btn_pause.setToolTip("兩支影片同步暫停 (空白鍵 Space)")
        self.btn_pause.setFixedWidth(115)
        self.btn_pause.setStyleSheet(
            "QPushButton { background-color: #ea580c; color: white; border: none; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; } "
            "QPushButton:hover { background-color: #c2410c; } "
            "QPushButton:pressed { background-color: #9a3412; }"
        )
        self.btn_pause.clicked.connect(self.pause_sync)
        center_btns_layout.addWidget(self.btn_pause)

        # 單格前進
        self.btn_step_forward = QPushButton("下一格 ▶")
        self.btn_step_forward.setToolTip("兩支影片同步前進約一幀 (33ms)")
        self.btn_step_forward.setStyleSheet(
            "QPushButton { background-color: #334155; color: #cbd5e1; border: 1px solid #475569; "
            "border-radius: 5px; padding: 7px 10px; font-size: 12px; } "
            "QPushButton:hover { background-color: #475569; }"
        )
        self.btn_step_forward.clicked.connect(lambda: self.step_sync(+33))
        center_btns_layout.addWidget(self.btn_step_forward)

        # 循環播放
        self.chk_loop = QCheckBox("循環播放")
        self.chk_loop.setChecked(True)
        self.chk_loop.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: bold; margin-left: 6px;")
        center_btns_layout.addWidget(self.chk_loop)

        # 0 距離緊貼模式開關
        self.chk_zero_gap = QCheckBox("0 距離貼合")
        self.chk_zero_gap.setChecked(True)
        self.chk_zero_gap.setToolTip("自動去除兩側黑邊，讓左右兩支影片畫面邊緣 0 距離完全無縫貼合")
        self.chk_zero_gap.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: bold; margin-left: 4px;")
        self.chk_zero_gap.toggled.connect(self.video_stage.set_zero_gap_mode)
        center_btns_layout.addWidget(self.chk_zero_gap)

        # 滿版放大開關 (全螢幕最大化 / 消除上下黑邊)
        self.chk_fill_screen = QCheckBox("滿版放大")
        self.chk_fill_screen.setChecked(False)
        self.chk_fill_screen.setToolTip("將畫面放大至填滿整個螢幕，消除所有黑邊")
        self.chk_fill_screen.setStyleSheet("color: #a78bfa; font-size: 12px; font-weight: bold; margin-left: 4px;")
        self.chk_fill_screen.toggled.connect(self.video_stage.set_fill_screen_mode)
        center_btns_layout.addWidget(self.chk_fill_screen)

        # 顯示 1px 對比輔助線開關
        self.chk_divider = QCheckBox("1px 參考線")
        self.chk_divider.setChecked(False)
        self.chk_divider.setToolTip("在兩支影片無縫接合處顯示極細 1px 亮線作為對齊參考")
        self.chk_divider.setStyleSheet("color: #cbd5e1; font-size: 12px; margin-left: 4px;")
        self.chk_divider.toggled.connect(self.video_stage.set_divider_visible)
        center_btns_layout.addWidget(self.chk_divider)

        # 播放倍速
        lbl_speed = QLabel("倍速:")
        lbl_speed.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 6px;")
        center_btns_layout.addWidget(lbl_speed)

        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.25x", "0.5x", "1.0x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.setStyleSheet(
            "QComboBox { background-color: #334155; color: #f8fafc; border: 1px solid #475569; "
            "border-radius: 4px; padding: 4px 8px; font-weight: bold; font-size: 12px; } "
            "QComboBox QAbstractItemView { background-color: #1e293b; color: #f8fafc; selection-background-color: #0284c7; }"
        )
        self.combo_speed.currentTextChanged.connect(self.change_playback_rate)
        center_btns_layout.addWidget(self.combo_speed)

        # ★ 全螢幕切換按鈕 (YouTube 風格)
        self.btn_fullscreen = QPushButton("⛶ 全螢幕 (F)")
        self.btn_fullscreen.setToolTip("進入全螢幕播放模式 (快捷鍵: F 或雙擊畫面，Esc 退出)")
        self.btn_fullscreen.setStyleSheet(
            "QPushButton { background-color: #0284c7; color: white; border: 1px solid #38bdf8; "
            "border-radius: 5px; padding: 7px 14px; font-size: 12px; font-weight: bold; margin-left: 8px; } "
            "QPushButton:hover { background-color: #0369a1; border-color: #7dd3fc; } "
            "QPushButton:pressed { background-color: #075985; }"
        )
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        center_btns_layout.addWidget(self.btn_fullscreen)

        ctrl_layout.addLayout(center_btns_layout)
        bottom_panel_layout.addWidget(center_control_frame)
        self.main_layout.addWidget(self.bottom_control_panel)

    def create_channel_ctrl_frame(self, channel: VideoChannel) -> QFrame:
        """建立個別影片下方的起始時間控制欄位"""
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 4px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 第一行：當前時間與總時間
        channel.lbl_time_status = QLabel("當前時間: 00:00.000 / 00:00.000")
        channel.lbl_time_status.setStyleSheet("color: #cbd5e1; font-size: 12px; font-family: Consolas, monospace;")
        layout.addWidget(channel.lbl_time_status)

        # 第二行：起始時間點輸入與微調
        row = QHBoxLayout()
        row.setSpacing(6)

        lbl_start = QLabel("起始時間點:")
        lbl_start.setStyleSheet("color: #f1f5f9; font-weight: bold; font-size: 12px;")
        row.addWidget(lbl_start)

        channel.edit_start_time = QLineEdit("00:00.000")
        channel.edit_start_time.setToolTip("輸入開始時間，支援 mm:ss.sss 或純秒數 (例如 5.2 或 00:05.200)")
        channel.edit_start_time.setStyleSheet(
            "QLineEdit { background-color: #0f172a; color: #38bdf8; border: 1px solid #475569; "
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; font-family: Consolas, monospace; "
            "font-weight: bold; min-width: 90px; } "
            "QLineEdit:focus { border: 1px solid #38bdf8; }"
        )
        channel.edit_start_time.returnPressed.connect(channel.apply_start_time)
        row.addWidget(channel.edit_start_time)

        btn_apply = QPushButton("套用")
        btn_apply.setToolTip("將此時間套用為起始點並跳轉預覽畫面")
        btn_apply.setStyleSheet(
            "QPushButton { background-color: #0284c7; color: white; border: none; "
            "border-radius: 4px; padding: 3px 8px; font-weight: bold; font-size: 12px; } "
            "QPushButton:hover { background-color: #0369a1; }"
        )
        btn_apply.clicked.connect(channel.apply_start_time)
        row.addWidget(btn_apply)

        btn_set_curr = QPushButton("📌 設為當前")
        btn_set_curr.setToolTip("將目前暫停的畫面時間填入起始時間點")
        btn_set_curr.setStyleSheet(
            "QPushButton { background-color: #334155; color: #e2e8f0; border: 1px solid #475569; "
            "border-radius: 4px; padding: 3px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #475569; color: white; }"
        )
        btn_set_curr.clicked.connect(channel.set_current_as_start)
        row.addWidget(btn_set_curr)

        # 微調按鈕
        for delta, label_text in [(-1000, "-1s"), (-100, "-0.1s"), (+100, "+0.1s"), (+1000, "+1s")]:
            btn_step = QPushButton(label_text)
            btn_step.setFixedWidth(38)
            btn_step.setStyleSheet(
                "QPushButton { background-color: #334155; color: #94a3b8; border: 1px solid #475569; "
                "border-radius: 4px; padding: 3px 0px; font-size: 11px; } "
                "QPushButton:hover { background-color: #475569; color: white; }"
            )
            btn_step.clicked.connect(lambda checked, d=delta, ch=channel: ch.step_start_time(d))
            row.addWidget(btn_step)

        row.addStretch()
        layout.addLayout(row)
        return frame

    def setup_sync_timer(self):
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(100)
        self.sync_timer.timeout.connect(self.on_sync_timer_tick)
        self.sync_timer.start()

    def setup_fullscreen_helpers(self):
        self.hide_timer = QTimer(self)
        self.hide_timer.setInterval(2500)
        self.hide_timer.timeout.connect(self.on_hide_timer_timeout)

        # 浮動控制面板透明度動畫
        self.panel_opacity_effect = QGraphicsOpacityEffect(self.bottom_control_panel)
        self.panel_opacity_effect.setOpacity(1.0)
        self.bottom_control_panel.setGraphicsEffect(self.panel_opacity_effect)

        self.fade_anim = QPropertyAnimation(self.panel_opacity_effect, b"opacity")
        self.fade_anim.setDuration(260)

        # 監聽全域事件以捕捉滑鼠活動
        qApp = QApplication.instance()
        if qApp:
            qApp.installEventFilter(self)

    def is_input_focused(self) -> bool:
        focus = QApplication.focusWidget()
        return isinstance(focus, QLineEdit)

    def on_shortcut_space(self):
        if not self.is_input_focused():
            self.toggle_play_pause()

    def on_shortcut_left(self):
        if not self.is_input_focused():
            self.step_sync(-100)

    def on_shortcut_right(self):
        if not self.is_input_focused():
            self.step_sync(+100)

    def on_shortcut_home(self):
        if not self.is_input_focused():
            self.reset_to_start()

    def on_shortcut_f(self):
        if not self.is_input_focused():
            self.toggle_fullscreen()

    def on_shortcut_esc(self):
        if self.is_fullscreen_mode:
            self.exit_fullscreen()

    def setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.on_shortcut_space)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.on_shortcut_left)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.on_shortcut_right)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self.on_shortcut_home)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.on_shortcut_f)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.on_shortcut_esc)

    def eventFilter(self, watched, event):
        if self.is_fullscreen_mode:
            ev_type = event.type()
            if ev_type in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                self.on_user_activity()
        return super().eventFilter(watched, event)

    def on_user_activity(self):
        if not self.is_fullscreen_mode:
            return
        self.unsetCursor()
        # 顯示浮動面板 (如果正處於淡出或隱藏狀態，淡入至 0.88 半透明)
        if not self.bottom_control_panel.isVisible() or self.panel_opacity_effect.opacity() < 0.85:
            self.bottom_control_panel.show()
            self.bottom_control_panel.raise_()
            self.fade_anim.stop()
            self.fade_anim.setStartValue(self.panel_opacity_effect.opacity())
            self.fade_anim.setEndValue(0.92)
            self.fade_anim.start()
        self.reset_hide_timer()

    def reset_hide_timer(self):
        if self.is_fullscreen_mode:
            self.hide_timer.start(2500)

    def on_hide_timer_timeout(self):
        if not self.is_fullscreen_mode:
            return
        # 若滑鼠指針在浮動控制面板上方，不隱藏
        cursor_pos = self.centralWidget().mapFromGlobal(QCursor.pos())
        if self.bottom_control_panel.geometry().contains(cursor_pos):
            self.hide_timer.start(2000)
            return
        # 若正在拖曳時間滑桿或正在打字，不隱藏
        if self.is_user_scrubbing or any(ch.edit_start_time.hasFocus() for ch in [self.channel_left, self.channel_right]):
            self.hide_timer.start(2000)
            return

        # 平滑淡出隱藏 (YouTube 風格)
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.panel_opacity_effect.opacity())
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.start()
        # 當淡出結束時隱藏面板
        QTimer.singleShot(270, lambda: self.bottom_control_panel.hide() if self.is_fullscreen_mode and self.panel_opacity_effect.opacity() == 0.0 else None)
        self.setCursor(Qt.CursorShape.BlankCursor)

    def toggle_fullscreen(self):
        if self.is_fullscreen_mode:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        self.is_fullscreen_mode = True
        self.top_bar_widget.hide()
        self.main_layout.removeWidget(self.bottom_control_panel)
        self.bottom_control_panel.setParent(self.centralWidget())
        # 半透明磨砂質感風格
        self.bottom_control_panel.setStyleSheet("""
            QFrame#bottomControlPanel {
                background-color: rgba(15, 23, 42, 0.78);
                border: 1px solid rgba(56, 189, 248, 0.5);
                border-radius: 12px;
                padding: 8px 14px;
            }
        """)
        self.panel_opacity_effect.setOpacity(0.92)
        self.video_stage.setStyleSheet("background-color: #000000; border: none; margin: 0px; padding: 0px;")
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.btn_fullscreen.setText("🗗 退出全螢幕 (F)")
        self.btn_fullscreen.setToolTip("退出全螢幕模式 (快捷鍵: Esc、F 或雙擊畫面)")
        self.btn_fullscreen.setStyleSheet(
            "QPushButton { background-color: #475569; color: white; border: 1px solid #94a3b8; "
            "border-radius: 5px; padding: 7px 14px; font-size: 12px; font-weight: bold; margin-left: 8px; } "
            "QPushButton:hover { background-color: #64748b; } "
            "QPushButton:pressed { background-color: #334155; }"
        )
        self.showFullScreen()
        self.bottom_control_panel.show()
        self.bottom_control_panel.raise_()
        self.update_overlay_geometry()
        self.video_stage.update_layout()
        self.video_stage.setFocus()
        self.reset_hide_timer()

    def exit_fullscreen(self):
        self.is_fullscreen_mode = False
        self.hide_timer.stop()
        self.fade_anim.stop()
        self.unsetCursor()
        self.panel_opacity_effect.setOpacity(1.0)
        self.bottom_control_panel.setStyleSheet("QFrame#bottomControlPanel { background: transparent; border: none; }")
        self.video_stage.setStyleSheet("background-color: #05070c; border: 1px solid #334155; border-radius: 6px;")
        self.main_layout.setContentsMargins(10, 8, 10, 8)
        self.main_layout.setSpacing(6)
        self.main_layout.addWidget(self.bottom_control_panel)
        self.top_bar_widget.show()
        self.bottom_control_panel.show()
        self.btn_fullscreen.setText("⛶ 全螢幕 (F)")
        self.btn_fullscreen.setToolTip("進入全螢幕播放模式 (快捷鍵: F 或雙擊畫面)")
        self.btn_fullscreen.setStyleSheet(
            "QPushButton { background-color: #0284c7; color: white; border: 1px solid #38bdf8; "
            "border-radius: 5px; padding: 7px 14px; font-size: 12px; font-weight: bold; margin-left: 8px; } "
            "QPushButton:hover { background-color: #0369a1; border-color: #7dd3fc; } "
            "QPushButton:pressed { background-color: #075985; }"
        )
        self.showNormal()
        self.video_stage.update_layout()

    def update_overlay_geometry(self):
        if not self.is_fullscreen_mode:
            return
        cw = self.centralWidget()
        w = cw.width()
        h = cw.height()
        if w <= 0 or h <= 0:
            return
        panel_w = min(1280, max(840, w - 48))
        panel_h = self.bottom_control_panel.sizeHint().height()
        if panel_h < 130:
            panel_h = 145
        panel_x = (w - panel_w) // 2
        panel_y = h - panel_h - 18
        self.bottom_control_panel.setGeometry(panel_x, panel_y, panel_w, panel_h)
        self.bottom_control_panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "is_fullscreen_mode", False):
            self.update_overlay_geometry()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if not self.isFullScreen() and getattr(self, "is_fullscreen_mode", False):
                self.exit_fullscreen()
        super().changeEvent(event)

    def toggle_play_pause(self):
        if self.is_playing:
            self.pause_sync()
        else:
            self.play_sync()

    def play_sync(self):
        self.channel_left.apply_start_time()
        self.channel_right.apply_start_time()

        rate = float(self.combo_speed.currentText().replace("x", ""))
        self.channel_left.player.setPlaybackRate(rate)
        self.channel_right.player.setPlaybackRate(rate)

        if self.sync_duration_ms > 0 and self.relative_pos_ms >= self.sync_duration_ms:
            self.relative_pos_ms = 0
            self.seek_both(0)

        self.channel_left.player.play()
        self.channel_right.player.play()
        self.is_playing = True
        self.btn_play.setStyleSheet(
            "QPushButton { background-color: #15803d; color: #bbf7d0; border: 2px solid #86efac; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; }"
        )
        self.btn_pause.setStyleSheet(
            "QPushButton { background-color: #ea580c; color: white; border: none; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; } "
            "QPushButton:hover { background-color: #c2410c; }"
        )

    def pause_sync(self):
        self.channel_left.player.pause()
        self.channel_right.player.pause()
        self.is_playing = False
        self.btn_play.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; border: none; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
        self.btn_pause.setStyleSheet(
            "QPushButton { background-color: #9a3412; color: #ffedd5; border: 2px solid #fdba74; "
            "border-radius: 5px; padding: 9px 16px; font-size: 14px; font-weight: bold; }"
        )

    def reset_to_start(self):
        self.pause_sync()
        self.channel_left.apply_start_time()
        self.channel_right.apply_start_time()
        self.relative_pos_ms = 0
        self.seek_both(0)
        self.update_slider_ui(0)

    def seek_both(self, rel_ms: int):
        self.relative_pos_ms = max(0, rel_ms)
        target1 = self.channel_left.start_offset_ms + self.relative_pos_ms
        target2 = self.channel_right.start_offset_ms + self.relative_pos_ms

        if self.channel_left.duration_ms > 0:
            target1 = min(target1, self.channel_left.duration_ms)
        if self.channel_right.duration_ms > 0:
            target2 = min(target2, self.channel_right.duration_ms)

        self.channel_left.player.setPosition(int(target1))
        self.channel_right.player.setPosition(int(target2))

    def step_sync(self, delta_ms: int):
        self.pause_sync()
        new_rel = max(0, self.relative_pos_ms + delta_ms)
        if self.sync_duration_ms > 0:
            new_rel = min(new_rel, self.sync_duration_ms)
        self.seek_both(new_rel)
        self.update_slider_ui(new_rel)

    def change_playback_rate(self, speed_str: str):
        rate = float(speed_str.replace("x", ""))
        self.channel_left.player.setPlaybackRate(rate)
        self.channel_right.player.setPlaybackRate(rate)

    def recalculate_timeline(self):
        left_avail = max(0, self.channel_left.duration_ms - self.channel_left.start_offset_ms)
        right_avail = max(0, self.channel_right.duration_ms - self.channel_right.start_offset_ms)

        if left_avail > 0 and right_avail > 0:
            self.sync_duration_ms = max(left_avail, right_avail)
        else:
            self.sync_duration_ms = max(left_avail, right_avail, 1000)

        self.timeline_slider.setRange(0, int(self.sync_duration_ms))
        self.lbl_sync_total.setText(format_ms_to_time(self.sync_duration_ms))
        self.update_slider_ui(self.relative_pos_ms)

    def update_slider_ui(self, rel_ms: int):
        if not self.is_user_scrubbing:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(int(rel_ms))
            self.timeline_slider.blockSignals(False)
        self.lbl_sync_current.setText(format_ms_to_time(rel_ms))

    def on_slider_pressed(self):
        self.is_user_scrubbing = True
        self.was_playing_before_scrub = self.is_playing
        if self.is_playing:
            self.pause_sync()

    def on_slider_moved(self, value: int):
        self.relative_pos_ms = value
        self.lbl_sync_current.setText(format_ms_to_time(value))
        self.seek_both(value)

    def on_slider_released(self):
        self.is_user_scrubbing = False
        self.relative_pos_ms = self.timeline_slider.value()
        self.seek_both(self.relative_pos_ms)
        if getattr(self, "was_playing_before_scrub", False):
            self.play_sync()

    def on_sync_timer_tick(self):
        if not self.is_playing or self.is_user_scrubbing:
            return

        pos1 = self.channel_left.player.position()
        pos2 = self.channel_right.player.position()

        rel1 = pos1 - self.channel_left.start_offset_ms
        rel2 = pos2 - self.channel_right.start_offset_ms

        ref_rel = rel1 if self.channel_left.duration_ms > 0 else rel2
        self.relative_pos_ms = max(0, ref_rel)
        self.update_slider_ui(self.relative_pos_ms)

        # 偏差校正
        if self.channel_left.duration_ms > 0 and self.channel_right.duration_ms > 0:
            diff = abs(rel1 - rel2)
            if diff > 80:
                target_pos2 = self.channel_right.start_offset_ms + ref_rel
                self.channel_right.player.setPosition(int(target_pos2))

        # 結尾檢查
        if self.sync_duration_ms > 0 and self.relative_pos_ms >= self.sync_duration_ms:
            if self.chk_loop.isChecked():
                self.reset_to_start()
                self.play_sync()
            else:
                self.pause_sync()

    def on_video_loaded(self, channel_id: int):
        self.recalculate_timeline()
        self.video_stage.update_layout()

    def try_auto_load_local_videos(self):
        cwd = os.getcwd()
        mp4_files = glob.glob(os.path.join(cwd, "*.mp4")) + glob.glob(os.path.join(cwd, "*.mkv"))
        if len(mp4_files) >= 2:
            file1 = None
            file2 = None
            for f in mp4_files:
                basename = os.path.basename(f)
                if "output" in basename.lower() or "{" in basename:
                    file2 = f
                elif "cut" in basename.lower() or "s-" in basename.lower():
                    file1 = f

            if not file1 or not file2:
                file1 = mp4_files[0]
                file2 = mp4_files[1]

            if file1 and file2 and file1 != file2:
                self.channel_left.load_video(file1)
                self.channel_right.load_video(file2)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("雙影片同步對比播放器")
    window = DualVideoPlayerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
