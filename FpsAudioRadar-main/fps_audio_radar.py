#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 FPS 听障辅助音频雷达  (FPS Audio Radar for Hearing Impaired)
 透明背景版 — 只显示雷达线条和光点，无黑色背景
=====================================================================
 功能：捕获系统输出音频（WASAPI 回环），实时分析声道方向，
       以透明悬浮雷达显示声音来源方向和类型。
       支持立体声 / 四声道 / 5.1 / 7.1 环绕，多声道自动 360° 定位。
 依赖：pip install numpy pyaudiowpatch PyQt5
 运行：python fps_audio_radar.py
 操作：左键拖动窗口  |  右键退出  |  滚轮调节整体透明度
=====================================================================
"""

import sys
import math
import time
import threading
import platform
import numpy as np
import pyaudiowpatch as pyaudio
from collections import deque

from PyQt5.QtWidgets import (QApplication, QWidget, QSystemTrayIcon,
                             QMenu, QAction, QStyle, QDialog, QFormLayout,
                             QDoubleSpinBox, QPushButton, QVBoxLayout, QLabel,
                             QComboBox, QHBoxLayout)
from PyQt5.QtCore    import Qt, QTimer, QPointF, QRectF
from PyQt5.QtGui     import (QPainter, QColor, QPen, QBrush, QFont,
                             QRadialGradient, QLinearGradient, QIcon)

# ============================ 点击穿透 (Windows) ============================
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    _GWL_EXSTYLE       = -20
    _WS_EX_LAYERED     = 0x00080000
    _WS_EX_TRANSPARENT = 0x00000020
    _user32            = ctypes.windll.user32

def set_click_through(hwnd, enable):
    """让窗口鼠标事件穿透到下层窗口（enable=True）或恢复正常（False）"""
    if not IS_WINDOWS or not hwnd:
        return
    styles = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enable:
        styles |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
    else:
        styles &= ~_WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, styles)

# ============================ 全局配置 ============================
SAMPLE_RATE    = 48000
BLOCK_SIZE     = 2048
RADAR_DIAMETER = 340
MARGIN         = 16
MAX_EVENTS     = 30
EVENT_LIFETIME = 1.4
NOISE_GATE     = 0.008
DIRECTION_GAIN = 1.0
SENSITIVITY    = 1.3
SNR_THRESHOLD  = 1.8

# ===== 运行时可配置参数 =====
CONFIG = {
    "sensitivity":      SENSITIVITY,
    "snr_threshold":    SNR_THRESHOLD,
    "direction_gain":   DIRECTION_GAIN,
    "noise_gate":       NOISE_GATE,
    "event_lifetime":   EVENT_LIFETIME,
    "shape":            "circle",      # 默认圆形雷达
    "center_dot_size":  1,
    "point_size":       1.0,           # 光点大小倍率
    "crosshair_style":  "cross",       # cross / dot / off
    "crosshair_size":   10,            # 准星尺寸（像素，十字线长/圆点半径基准）
    "crosshair_color":  "#FF5252",     # 准星颜色
    "device_index":     None,          # None = 自动选择默认回环
}

# 低/中/高 一键预设
PRESETS = {
    "低": {"sensitivity": 0.8,  "snr_threshold": 2.5, "direction_gain": 1.2, "noise_gate": 0.015},
    "中": {"sensitivity": 1.3,  "snr_threshold": 1.8, "direction_gain": 1.0, "noise_gate": 0.008},
    "高": {"sensitivity": 2.0,  "snr_threshold": 1.2, "direction_gain": 1.5, "noise_gate": 0.003},
}

SHAPE_NAMES = {"circle": "圆形雷达", "semicircle": "半圆形雷达", "hbar": "水平方向条"}
CROSSHAIR_NAMES = {"cross": "十字准星", "dot": "圆点准星", "off": "关闭准星"}
CROSSHAIR_COLORS = [
    ("#66BB6A", "绿色"),
    ("#FF5252", "红色"),
    ("#448AFF", "蓝色"),
    ("#FFD740", "黄色"),
    ("#FFFFFF", "白色"),
    ("#18FFFF", "青色"),
    ("#FF9100", "橙色"),
    ("#E040FB", "紫色"),
]

# 声音类型
SOUND_TYPES = [
    {"key": "explosion", "name": "爆炸", "color": "#FF6D00", "freq": (20, 120),  "transient": False},
    {"key": "footstep",  "name": "脚步", "color": "#40C4FF", "freq": (70, 300),  "transient": False},
    {"key": "gunshot",   "name": "枪响", "color": "#FF1744", "freq": (150, 5000),"transient": True},
    {"key": "door",      "name": "开门", "color": "#E040FB", "freq": (280, 2600),"transient": False},
    {"key": "unknown",   "name": "其他", "color": "#B0BEC5", "freq": (0, 20000), "transient": False},
]

def type_info(key):
    for s in SOUND_TYPES:
        if s["key"] == key:
            return s
    return SOUND_TYPES[-1]

# 窗口尺寸
WIN_W = RADAR_DIAMETER + MARGIN * 2
WIN_H = WIN_W

# 7.1 声道角度映射（游戏方位角，度，0°=正前，顺时针为正）
# 标准布局: FL, FR, C, LFE, SL, SR, BL, BR
CHANNEL_ANGLES_71 = {
    0: 315.0,   # FL 左前
    1: 45.0,    # FR 右前
    2: 0.0,     # C  中置(正前)
    3: None,    # LFE 低音炮(无方向)
    4: 270.0,   # SL 左环绕(正左)
    5: 90.0,    # SR 右环绕(正右)
    6: 225.0,   # BL 左后
    7: 135.0,   # BR 右后
}

# 5.1 声道角度映射: FL, FR, C, LFE, SL, SR
CHANNEL_ANGLES_51 = {
    0: 315.0, 1: 45.0, 2: 0.0, 3: None, 4: 270.0, 5: 90.0,
}

# 四声道角度映射: FL, FR, BL, BR
CHANNEL_ANGLES_4 = {
    0: 315.0, 1: 45.0, 2: 225.0, 3: 135.0,
}


# ============================ 音频分析 ============================
class AudioAnalyzer:
    def __init__(self, on_event):
        self.on_event    = on_event
        self.p           = None
        self.stream      = None
        self.running     = False
        self.rms_hist    = deque(maxlen=60)
        self.noise_floor = CONFIG["noise_gate"]
        self.noise_alpha = 0.96
        self.sample_rate = SAMPLE_RATE
        self.channels    = 2
        self.device_name = ""
        self.is_loopback = False
        self.smoothed_dir = 0.0       # 方向平滑
        self.smoothed_angle = 90.0    # 方位角平滑(度)
        self.dir_smooth_alpha = 0.35  # 平滑系数(越小越稳)
        self.angle_initialized = False # 方位角是否已初始化(首次直接赋值)

    def _callback(self, in_data, frame_count, time_info, status):
        """pyaudiowpatch 回调：in_data 是原始字节"""
        audio = np.frombuffer(in_data, dtype=np.int16).astype(np.float64) / 32768.0
        if self.channels > 1:
            audio = audio.reshape(-1, self.channels)
        else:
            audio = audio.reshape(-1, 1)
        result = self._analyze(audio)
        if result is not None:
            self.on_event(result)
        return (None, pyaudio.paContinue)

    def _compute_direction_stereo(self, left, right):
        """
        立体声方向计算（增强版）：
        1. 能量差（高频可靠）
        2. 互相关ITD（低频可靠，时间差）
        3. 多频段加权融合
        返回 direction (-1..1)，-1=左，1=右
        """
        n = len(left)
        sr = self.sample_rate

        # ---- 能量差方向 ----
        rms_l = float(np.sqrt(np.mean(left ** 2)))
        rms_r = float(np.sqrt(np.mean(right ** 2)))
        denom = rms_l + rms_r + 1e-9
        dir_energy = (rms_r - rms_l) / denom

        # ---- 互相关 ITD 方向 ----
        # 搜索范围 ±40 样本（48kHz 下约 ±0.83ms，覆盖人头最大ITD）
        max_lag = min(40, n // 4)
        # 归一化互相关
        left_n = left - np.mean(left)
        right_n = right - np.mean(right)
        corr = np.correlate(left_n, right_n, mode='full')
        mid = len(corr) // 2
        search = corr[mid - max_lag:mid + max_lag + 1]
        best_lag = np.argmax(search) - max_lag  # 正=右超前(声音从右来)
        # 归一化到 -1..1，最大lag对应极端方向
        dir_itd = float(np.clip(best_lag / max_lag, -1.0, 1.0))

        # ---- 多频段方向 ----
        # 分低/中/高三段，分别算能量差方向
        mono = (left + right) / 2.0
        fft_l = np.abs(np.fft.rfft(left * np.hanning(n)))
        fft_r = np.abs(np.fft.rfft(right * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1.0 / sr)

        def band_dir(lo, hi):
            m = (freqs >= lo) & (freqs <= hi)
            el = float(np.sum(fft_l[m])) + 1e-9
            er = float(np.sum(fft_r[m])) + 1e-9
            return (er - el) / (el + er)

        dir_low  = band_dir(20, 300)     # 低频：ITD更可靠
        dir_mid  = band_dir(300, 3000)   # 中频：混合
        dir_high = band_dir(3000, 12000) # 高频：能量差更可靠

        # 加权融合：高频用能量差，低频用ITD
        dir_fused = (
            dir_energy * 0.35 +          # 全局能量差
            dir_itd * 0.25 +             # 时间差
            dir_low * 0.10 +             # 低频能量差(辅助)
            dir_mid * 0.15 +             # 中频
            dir_high * 0.15              # 高频(脚步声主要频段)
        )

        # 应用方向增益并裁剪
        direction = float(np.clip(dir_fused * CONFIG["direction_gain"], -1.0, 1.0))

        # 指数平滑
        self.smoothed_dir = (self.smoothed_dir * (1 - self.dir_smooth_alpha)
                              + direction * self.dir_smooth_alpha)

        return self.smoothed_dir, rms_l, rms_r

    def _compute_angle_multichannel(self, block):
        """
        多声道(4/5.1/7.1) 360° 方位角计算。
        利用各声道能量加权向量和，返回 angle (0..360°，0=正右，90=正前)
        以及总能量 rms。
        坐标系：数学极坐标，0°=右，90°=上(前)，逆时针为正。
        这样和原立体声绘制逻辑一致。
        """
        ch = block.shape[1]
        rms_per_ch = []
        for c in range(ch):
            rms_per_ch.append(float(np.sqrt(np.mean(block[:, c] ** 2))))

        # 选择声道角度表
        if ch >= 8:
            angle_map = CHANNEL_ANGLES_71
        elif ch >= 6:
            angle_map = CHANNEL_ANGLES_51
        elif ch >= 4:
            angle_map = CHANNEL_ANGLES_4
        else:
            return None, 0.0  # 不足4声道，退回立体声模式

        # 加权向量和（游戏方位角：0°=正前，顺时针）
        # 转换为数学极坐标：math_angle = 90 - game_angle
        sum_x = 0.0  # 数学坐标 x (右)
        sum_y = 0.0  # 数学坐标 y (上/前)
        total_e = 0.0

        for c in range(ch):
            ang_game = angle_map.get(c)
            if ang_game is None:
                continue  # LFE 跳过
            e = rms_per_ch[c]
            if e < 1e-6:
                continue
            # 游戏角(0=前,顺时针) → 数学角(0=右,逆时针)
            math_ang = math.radians(90.0 - ang_game)
            sum_x += e * math.cos(math_ang)
            sum_y += e * math.sin(math_ang)
            total_e += e

        if total_e < 1e-6:
            return None, 0.0

        # 向量和角度（数学极坐标，度）
        angle_deg = math.degrees(math.atan2(sum_y, sum_x))
        if angle_deg < 0:
            angle_deg += 360.0

        # 向量模长 / 总能量 = 方向集中度 (0..1)
        concentration = math.sqrt(sum_x ** 2 + sum_y ** 2) / (total_e + 1e-9)
        concentration = float(np.clip(concentration, 0.0, 1.0))

        # 指数平滑方位角（处理角度环绕），首次直接赋值
        if not self.angle_initialized:
            self.smoothed_angle = angle_deg
            self.angle_initialized = True
        else:
            prev = self.smoothed_angle
            diff = angle_deg - prev
            while diff > 180:
                diff -= 360
            while diff < -180:
                diff += 360
            self.smoothed_angle = prev + diff * self.dir_smooth_alpha
            if self.smoothed_angle < 0:
                self.smoothed_angle += 360
            elif self.smoothed_angle >= 360:
                self.smoothed_angle -= 360

        rms = total_e / max(1, ch)
        return self.smoothed_angle, rms, concentration

    def _analyze(self, block):
        if block.ndim < 2 or block.shape[1] < 2:
            return None

        ch = block.shape[1]
        angle = None          # 数学极坐标角度(度)，None=用立体声direction
        direction = 0.0
        concentration = 1.0

        if ch >= 4:
            # 多声道模式：360° 定位
            result = self._compute_angle_multichannel(block)
            if result[0] is not None:
                angle, rms, concentration = result
                # 同时从 angle 反推 direction（用于兼容）
                # angle 90°=前, 0°=右, 180°=左
                if angle <= 180:
                    direction = (90 - angle) / 90.0  # 右→1, 前→0, 左→-1
                else:
                    direction = (90 - angle) / 90.0
                    direction = float(np.clip(direction, -1.0, 1.0))
            else:
                # 多声道计算失败，退回立体声
                left, right = block[:, 0], block[:, 1]
                direction, rms_l, rms_r = self._compute_direction_stereo(left, right)
                rms = (rms_l + rms_r) / 2.0
        else:
            # 立体声模式
            left, right = block[:, 0], block[:, 1]
            direction, rms_l, rms_r = self._compute_direction_stereo(left, right)
            rms = (rms_l + rms_r) / 2.0

        # ---- 自适应噪声底噪 ----
        if rms < self.noise_floor * 4.0:
            self.noise_floor = (self.noise_floor * self.noise_alpha
                                + rms * (1.0 - self.noise_alpha))
        self.noise_floor = max(self.noise_floor, CONFIG["noise_gate"] * 0.3)

        # ---- 信噪比触发 ----
        threshold = self.noise_floor * CONFIG["snr_threshold"] / CONFIG["sensitivity"]
        if rms < threshold:
            return None

        # 瞬态检测
        self.rms_hist.append(rms)
        transient_ratio = 1.0
        if len(self.rms_hist) >= 12:
            avg = float(np.mean(list(self.rms_hist)[:-6]))
            if avg > 1e-6:
                transient_ratio = rms / avg

        # 频谱 + 类型分类（用前两声道混合做单声道分析）
        mono = (block[:, 0] + block[:, 1]) / 2.0
        n = len(mono)
        fft_mag = np.abs(np.fft.rfft(mono * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)
        total_e = float(np.sum(fft_mag)) + 1e-9

        def band_energy(lo, hi):
            m = (freqs >= lo) & (freqs <= hi)
            return float(np.sum(fft_mag[m])) if np.any(m) else 0.0

        best_key, best_score = "unknown", 0.0
        for st in SOUND_TYPES[:-1]:
            ratio = band_energy(*st["freq"]) / total_e
            score = ratio * (1.6 if st["transient"] and transient_ratio > 2.2 else 1.0)
            if score > best_score:
                best_score, best_key = score, st["key"]

        snr = rms / (self.noise_floor + 1e-9)
        intensity = float(np.clip((snr - CONFIG["snr_threshold"]) / 6.0, 0.0, 1.0))
        # 多声道方向集中度也影响强度（方向越明确越亮）
        if ch >= 4:
            intensity = intensity * (0.5 + 0.5 * concentration)

        return {
            "direction": direction,
            "angle": angle,           # 数学极坐标角度(度)，None=立体声模式
            "intensity": intensity,
            "type": best_key,
            "time": time.time(),
            "channels": ch,
        }

    def _open_stream(self, dev_idx, channels, rate, label):
        """通用打开流方法"""
        self.channels = max(2, channels)
        self.sample_rate = int(rate)
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            frames_per_buffer=BLOCK_SIZE,
            input=True,
            input_device_index=dev_idx,
            stream_callback=self._callback,
        )
        self.stream.start_stream()
        self.running = True
        print(f"[音频] 系统音频捕获已启动 ({label})")
        print(f"[音频] 采样率: {self.sample_rate}, 通道: {self.channels}")
        return True

    def _get_loopback_devices(self):
        """获取所有可用的 WASAPI 回环设备列表"""
        devices = []
        try:
            # 方法1: get_loopback_device_info_generator
            if hasattr(self.p, "get_loopback_device_info_generator"):
                for lb in self.p.get_loopback_device_info_generator():
                    devices.append(lb)
        except Exception:
            pass

        if not devices:
            # 方法2: 遍历设备列表找 loopback
            try:
                for i in range(self.p.get_device_count()):
                    dev = self.p.get_device_info_by_index(i)
                    name = dev["name"].lower()
                    if "loopback" in name and dev.get("maxInputChannels", 0) >= 2:
                        devices.append(dev)
            except Exception:
                pass
        return devices

    def start(self):
        try:
            self.p = pyaudio.PyAudio()
        except Exception as e:
            print(f"[音频] pyaudiowpatch 初始化失败: {e}")
            return False

        # 如果用户指定了设备
        if CONFIG.get("device_index") is not None:
            try:
                dev = self.p.get_device_info_by_index(CONFIG["device_index"])
                ch = max(2, dev.get("maxInputChannels", dev.get("maxOutputChannels", 2)))
                rate = int(dev["defaultSampleRate"])
                self.device_name = dev["name"]
                self.is_loopback = "loopback" in dev["name"].lower()
                return self._open_stream(CONFIG["device_index"], ch, rate, f"指定: {dev['name']}")
            except Exception as e:
                print(f"[音频] 指定设备打开失败: {e}，尝试自动选择")

        # ---- 策略1: get_default_wasapi_loopback (pyaudiowpatch 标准API) ----
        try:
            if hasattr(self.p, "get_default_wasapi_loopback"):
                lb = self.p.get_default_wasapi_loopback()
                ch = max(2, lb.get("maxInputChannels", 2))
                rate = int(lb["defaultSampleRate"])
                self.device_name = lb["name"]
                self.is_loopback = True
                return self._open_stream(lb["index"], ch, rate, f"默认回环: {lb['name']}")
        except Exception as e:
            print(f"[音频] get_default_wasapi_loopback 失败: {e}")

        # ---- 策略2: as_loopback 参数（新版 pyaudiowpatch）----
        try:
            wasapi_info = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
            dev_idx = wasapi_info["defaultOutputDevice"]
            dev_info = self.p.get_device_info_by_index(dev_idx)
            ch = max(2, dev_info.get("maxOutputChannels", 2))
            rate = int(dev_info["defaultSampleRate"])
            print(f"[音频] 尝试 as_loopback: {dev_info['name']}")
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=ch,
                rate=rate,
                frames_per_buffer=BLOCK_SIZE,
                input=True,
                input_device_index=dev_idx,
                as_loopback=True,
                stream_callback=self._callback,
            )
            self.stream.start_stream()
            self.running = True
            self.channels = ch
            self.sample_rate = rate
            self.device_name = dev_info["name"]
            self.is_loopback = True
            print(f"[音频] 系统音频捕获已启动 (as_loopback: {dev_info['name']})")
            return True
        except Exception as e:
            print(f"[音频] as_loopback 失败: {e}")

        # ---- 策略3: 枚举回环设备 ----
        try:
            devices = self._get_loopback_devices()
            if devices:
                lb = devices[0]
                ch = max(2, lb.get("maxInputChannels", 2))
                rate = int(lb["defaultSampleRate"])
                self.device_name = lb["name"]
                self.is_loopback = True
                return self._open_stream(lb["index"], ch, rate, f"枚举回环: {lb['name']}")
        except Exception as e:
            print(f"[音频] 回环枚举失败: {e}")

        # ---- 策略4: 默认输入设备（降级）----
        try:
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=SAMPLE_RATE,
                frames_per_buffer=BLOCK_SIZE,
                input=True,
                stream_callback=self._callback,
            )
            self.stream.start_stream()
            self.running = True
            self.channels = 2
            self.sample_rate = SAMPLE_RATE
            self.device_name = "默认输入设备"
            self.is_loopback = False
            print("[音频] 已降级到默认输入设备（可能是麦克风，录不到游戏声）")
            return True
        except Exception as e:
            print(f"[音频] 启动失败: {e}")
            return False

    def stop(self):
        self.running = False
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.p:
            try:
                self.p.terminate()
            except Exception:
                pass
            self.p = None

    def get_status(self):
        """返回音频状态描述"""
        if not self.running:
            return "音频未启动"
        ch_name = {2: "立体声", 4: "四声道", 6: "5.1", 8: "7.1"}.get(self.channels, f"{self.channels}声道")
        lb_tag = "回环" if self.is_loopback else "输入"
        return f"{self.device_name[:22]} | {ch_name} | {self.sample_rate//1000}k Hz | {lb_tag}"


# ============================ 雷达窗口 ============================
class RadarWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WIN_W, WIN_H)

        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - WIN_W) // 2,
                  (screen.height() - WIN_H) // 2)

        self.events        = []
        self.lock          = threading.Lock()
        self.opacity       = 0.95
        self.click_through = True
        self.setWindowOpacity(self.opacity)
        self.status_msg    = ""

        self.analyzer = AudioAnalyzer(self._on_audio_event)
        if not self.analyzer.start():
            self.status_msg = "音频捕获失败"

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(30)

        self.drag_pos = None

    # ---------- 音频事件 ----------
    def _on_audio_event(self, event):
        with self.lock:
            merged = False
            for ev in self.events:
                # 多声道模式用 angle 比较，立体声用 direction
                if ev["type"] == event["type"]:
                    if event.get("angle") is not None and ev.get("angle") is not None:
                        ang_diff = abs(ev["angle"] - event["angle"])
                        ang_diff = min(ang_diff, 360 - ang_diff)
                        dir_close = ang_diff < 30  # 30度内合并
                    else:
                        dir_close = abs(ev["direction"] - event["direction"]) < 0.25
                    if dir_close and time.time() - ev["time"] < 0.25:
                        if event["intensity"] > ev["intensity"]:
                            ev["intensity"] = event["intensity"]
                        ev["time"] = event["time"]
                        merged = True
                        break
            if not merged:
                self.events.append(event)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]

    def _tick(self):
        now = time.time()
        with self.lock:
            self.events = [e for e in self.events if now - e["time"] < CONFIG["event_lifetime"]]
        self.update()

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()
        elif e.button() == Qt.RightButton:
            self.close()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self.drag_pos is not None:
            self.move(e.globalPos() - self.drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self.drag_pos = None

    def wheelEvent(self, e):
        delta = 0.05 if e.angleDelta().y() > 0 else -0.05
        self.opacity = max(0.2, min(1.0, self.opacity + delta))
        self.setWindowOpacity(self.opacity)

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_click_through()

    def _apply_click_through(self):
        hwnd = int(self.winId())
        set_click_through(hwnd, self.click_through)

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self._apply_click_through()
        return self.click_through

    def restart_audio(self):
        """重启音频捕获（切换设备后调用）"""
        self.analyzer.stop()
        time.sleep(0.1)
        if not self.analyzer.start():
            self.status_msg = "音频捕获失败"
        else:
            self.status_msg = ""

    def closeEvent(self, e):
        self.analyzer.stop()
        super().closeEvent(e)

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        cx = WIN_W / 2
        cy = WIN_H / 2
        r  = RADAR_DIAMETER / 2
        shape = CONFIG.get("shape", "circle")

        self._draw_frame(p, cx, cy, r, shape)
        self._draw_events(p, cx, cy, r, shape)
        self._draw_crosshair(p, cx, cy, shape)

    def _draw_crosshair(self, p, cx, cy, shape):
        """绘制中心准星：十字/圆点/关闭"""
        style = CONFIG.get("crosshair_style", "cross")
        if style == "off":
            return
        size = CONFIG.get("crosshair_size", 10)
        color = QColor(CONFIG.get("crosshair_color", "#66BB6A"))
        color.setAlpha(230)

        if style == "dot":
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), size * 0.4, size * 0.4)
        elif style == "cross":
            # 十字准星：带中心缺口的四条线，游戏风格
            gap = size * 0.25   # 中心缺口
            arm = size           # 线长
            pen = QPen(color, 2)
            p.setPen(pen)
            # 上
            p.drawLine(QPointF(cx, cy - gap), QPointF(cx, cy - gap - arm))
            # 下
            p.drawLine(QPointF(cx, cy + gap), QPointF(cx, cy + gap + arm))
            # 左
            p.drawLine(QPointF(cx - gap, cy), QPointF(cx - gap - arm, cy))
            # 右
            p.drawLine(QPointF(cx + gap, cy), QPointF(cx + gap + arm, cy))
            # 中心微点
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), 1.2, 1.2)

    def _draw_frame(self, p, cx, cy, r, shape):
        if shape == "circle":
            self._draw_circle_frame(p, cx, cy, r)
        elif shape == "semicircle":
            self._draw_semicircle_frame(p, cx, cy, r)
        elif shape == "hbar":
            self._draw_hbar_frame(p, cx, cy, r)

    def _draw_circle_frame(self, p, cx, cy, r):
        pen = QPen(QColor(27, 94, 32, 220), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

        pen = QPen(QColor(27, 94, 32, 140), 1)
        p.setPen(pen)
        for i in range(1, 5):
            ri = r * i / 5
            p.drawEllipse(QPointF(cx, cy), ri, ri)

        pen = QPen(QColor(27, 94, 32, 120), 1)
        p.setPen(pen)
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        for ang in (45, 135):
            rad = math.radians(ang)
            p.drawLine(QPointF(cx, cy),
                       QPointF(cx + r * math.cos(rad), cy - r * math.sin(rad)))
            p.drawLine(QPointF(cx, cy),
                       QPointF(cx - r * math.cos(rad), cy + r * math.sin(rad)))

        p.setBrush(QColor(102, 187, 106, 230))
        p.setPen(Qt.NoPen)
        d = CONFIG.get("center_dot_size", 4)
        p.drawEllipse(QPointF(cx, cy), d, d)

    def _draw_semicircle_frame(self, p, cx, cy, r):
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)

        pen = QPen(QColor(27, 94, 32, 220), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, 180 * 16)

        pen = QPen(QColor(27, 94, 32, 140), 1)
        p.setPen(pen)
        for i in range(1, 5):
            ri = r * i / 5
            sub = QRectF(cx - ri, cy - ri, 2 * ri, 2 * ri)
            p.drawArc(sub, 0, 180 * 16)

        pen = QPen(QColor(27, 94, 32, 120), 1)
        p.setPen(pen)
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy - r))
        for ang in (45, 135):
            rad = math.radians(ang)
            p.drawLine(QPointF(cx, cy),
                       QPointF(cx + r * math.cos(rad), cy - r * math.sin(rad)))

        p.setBrush(QColor(102, 187, 106, 230))
        p.setPen(Qt.NoPen)
        d = CONFIG.get("center_dot_size", 4)
        p.drawEllipse(QPointF(cx, cy), d, d)

    def _draw_hbar_frame(self, p, cx, cy, r):
        bar_h = 20
        pen = QPen(QColor(27, 94, 32, 220), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(cx - r, cy - bar_h / 2, 2 * r, bar_h), 6, 6)

        pen = QPen(QColor(102, 187, 106, 200), 2)
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy - bar_h / 2 - 4), QPointF(cx, cy + bar_h / 2 + 4))

        pen = QPen(QColor(27, 94, 32, 120), 1)
        p.setPen(pen)
        for frac in (-0.75, -0.5, -0.25, 0.25, 0.5, 0.75):
            x = cx + r * frac
            p.drawLine(QPointF(x, cy - bar_h / 2), QPointF(x, cy - bar_h / 2 + 5))
            p.drawLine(QPointF(x, cy + bar_h / 2 - 5), QPointF(x, cy + bar_h / 2))

        p.setBrush(QColor(102, 187, 106, 230))
        p.setPen(Qt.NoPen)
        d = CONFIG.get("center_dot_size", 3)
        p.drawEllipse(QPointF(cx, cy), d, d)

    # ---------- 事件光点绘制 ----------
    def _draw_events(self, p, cx, cy, r, shape):
        now = time.time()
        p.setPen(Qt.NoPen)
        for e in self.events:
            age  = now - e["time"]
            fade = max(0.0, 1.0 - age / CONFIG["event_lifetime"])
            self._draw_point(p, e, cx, cy, r, shape, fade)

    def _draw_point(self, p, e, cx, cy, r, shape, fade):
        st   = type_info(e["type"])
        base = QColor(st["color"])
        size = (2 + e["intensity"] * 5) * CONFIG.get("point_size", 1.0)

        if shape == "hbar":
            px = cx + e["direction"] * (r * 0.92)
            py = cy
        else:
            # 优先使用多声道 angle（数学极坐标，度）
            if e.get("angle") is not None:
                math_ang = e["angle"]
            else:
                # 立体声：direction -1..1 → 左180° 前90° 右0°
                math_ang = 90 - e["direction"] * 90

            rad = math.radians(math_ang)
            # 距离：强度越大越近（越靠中心）
            dist = r * (1.0 - e["intensity"] * 0.75)
            dist = max(r * 0.12, dist)
            px = cx + dist * math.cos(rad)
            py = cy - dist * math.sin(rad)

        # 径向渐变光晕
        glow_size = size * 2.0
        grad = QRadialGradient(QPointF(px, py), glow_size)
        c1 = QColor(base)
        c1.setAlphaF(0.55 * fade)
        c2 = QColor(base)
        c2.setAlphaF(0.0)
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(px, py), glow_size, glow_size)

        # 实心主体
        body = QColor(base)
        body.setAlphaF(0.9 * fade)
        p.setBrush(body)
        p.drawEllipse(QPointF(px, py), size, size)

        # 白色核心
        core = QColor(255, 255, 255, int(220 * fade))
        p.setBrush(core)
        p.drawEllipse(QPointF(px, py), size * 0.35, size * 0.35)


# ============================ 设置对话框 ============================
class SettingsDialog(QDialog):
    FIELDS = [
        ("sensitivity",    "灵敏度",        0.5, 2.5, 0.1, 2),
        ("snr_threshold",  "信噪比阈值",    1.0, 5.0, 0.1, 2),
        ("direction_gain", "方向增益",      1.0, 3.0, 0.1, 2),
        ("noise_gate",     "噪声门下限",    0.001, 0.05, 0.001, 4),
        ("event_lifetime", "光点存活(秒)",  0.5, 3.0, 0.1, 2),
        ("point_size",     "光点大小",      0.5, 4.0, 0.1, 1),
    ]

    def __init__(self, parent=None, analyzer=None):
        super().__init__(parent)
        self.analyzer = analyzer
        self.setWindowTitle("雷达设置")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(320)

        layout = QVBoxLayout()
        form = QFormLayout()
        self.spins = {}

        # 音频设备选择
        self.device_combo = QComboBox()
        self._populate_devices()
        form.addRow("音频设备:", self.device_combo)

        # 形状选择
        self.shape_combo = QComboBox()
        for key, name in SHAPE_NAMES.items():
            self.shape_combo.addItem(name, key)
        idx = self.shape_combo.findData(CONFIG.get("shape", "circle"))
        if idx >= 0:
            self.shape_combo.setCurrentIndex(idx)
        form.addRow("雷达形状:", self.shape_combo)

        # 准星样式选择
        self.crosshair_combo = QComboBox()
        for key, name in CROSSHAIR_NAMES.items():
            self.crosshair_combo.addItem(name, key)
        idx = self.crosshair_combo.findData(CONFIG.get("crosshair_style", "cross"))
        if idx >= 0:
            self.crosshair_combo.setCurrentIndex(idx)
        form.addRow("准星样式:", self.crosshair_combo)

        # 准星大小
        self.crosshair_size_spin = QDoubleSpinBox()
        self.crosshair_size_spin.setRange(4, 30)
        self.crosshair_size_spin.setSingleStep(1)
        self.crosshair_size_spin.setDecimals(0)
        self.crosshair_size_spin.setValue(CONFIG.get("crosshair_size", 10))
        form.addRow("准星大小:", self.crosshair_size_spin)

        # 准星颜色
        self.crosshair_color_combo = QComboBox()
        for hex_val, name in CROSSHAIR_COLORS:
            self.crosshair_color_combo.addItem(name, hex_val)
        idx = self.crosshair_color_combo.findData(CONFIG.get("crosshair_color", "#66BB6A"))
        if idx >= 0:
            self.crosshair_color_combo.setCurrentIndex(idx)
        form.addRow("准星颜色:", self.crosshair_color_combo)

        # 预设按钮
        preset_row = QHBoxLayout()
        for level in ("低", "中", "高"):
            btn = QPushButton(f"预设:{level}")
            btn.clicked.connect(lambda checked, lv=level: self._apply_preset(lv))
            preset_row.addWidget(btn)
        form.addRow("一键预设:", preset_row)

        preset_hint = QLabel(
            "低  →  安静环境，防误触发\n"
            "中  →  通用平衡，默认推荐\n"
            "高  →  嘈杂环境，捕捉远声"
        )
        preset_hint.setStyleSheet(
            "color:#7cb342; font-size:15px; line-height:1.6; "
            "padding:4px 14px; background:rgba(27,94,32,0.15); "
            "border-radius:4px;"
        )
        form.addRow("", preset_hint)
        form.addRow(QLabel(""))

        # 数值框
        for key, label, lo, hi, step, decimals in self.FIELDS:
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(decimals)
            sp.setValue(CONFIG[key])
            self.spins[key] = sp
            form.addRow(label + ":", sp)

        layout.addLayout(form)

        hint = QLabel("修改后点应用，实时生效；切换设备需点应用后重启音频")
        hint.setStyleSheet("color:#888; font-size:11px;")
        layout.addWidget(hint)

        btn_layout = QFormLayout()
        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self._apply)
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self._apply_and_close)
        btn_layout.addRow(btn_apply, btn_ok)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _populate_devices(self):
        self.device_combo.addItem("自动选择(默认回环)", None)
        if self.analyzer and self.analyzer.p:
            try:
                for i in range(self.analyzer.p.get_device_count()):
                    dev = self.analyzer.p.get_device_info_by_index(i)
                    ch = dev.get("maxInputChannels", dev.get("maxOutputChannels", 0))
                    if ch >= 2:
                        name = dev["name"]
                        if len(name) > 40:
                            name = name[:37] + "..."
                        self.device_combo.addItem(f"{name} ({ch}ch)", dev["index"])
            except Exception:
                pass
        # 选中当前
        cur = CONFIG.get("device_index")
        if cur is not None:
            idx = self.device_combo.findData(cur)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)

    def _apply_preset(self, level):
        preset = PRESETS.get(level, {})
        for key, val in preset.items():
            if key in self.spins:
                self.spins[key].setValue(val)

    def _apply(self):
        CONFIG["shape"] = self.shape_combo.currentData()
        CONFIG["crosshair_style"] = self.crosshair_combo.currentData()
        CONFIG["crosshair_size"] = self.crosshair_size_spin.value()
        CONFIG["crosshair_color"] = self.crosshair_color_combo.currentData()
        CONFIG["device_index"] = self.device_combo.currentData()
        for key, *_ in self.FIELDS:
            CONFIG[key] = self.spins[key].value()

    def _apply_and_close(self):
        self._apply()
        self.accept()


# ============================ 入口 ============================
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    radar = RadarWidget()
    radar.show()

    # ---------- 系统托盘 ----------
    tray = QSystemTrayIcon()
    tray.setIcon(app.style().standardIcon(QStyle.SP_ComputerIcon))
    tray.setToolTip("FPS 音频雷达")

    menu = QMenu()

    act_toggle = QAction("切换点击穿透 (当前: 开)", menu)
    def _on_toggle():
        on = radar.toggle_click_through()
        act_toggle.setText(f"切换点击穿透 (当前: {'开' if on else '关'})")
    act_toggle.triggered.connect(_on_toggle)
    menu.addAction(act_toggle)

    act_hide = QAction("显示/隐藏雷达", menu)
    act_hide.triggered.connect(lambda: radar.setVisible(not radar.isVisible()))
    menu.addAction(act_hide)

    # 雷达形状子菜单
    shape_menu = menu.addMenu("雷达形状")
    shape_actions = {}
    def _make_shape_switch(key):
        def _switch():
            CONFIG["shape"] = key
            radar.update()
            for k, act in shape_actions.items():
                act.setChecked(k == key)
        return _switch
    for key, name in SHAPE_NAMES.items():
        act = QAction(name, shape_menu, checkable=True)
        act.setChecked(CONFIG.get("shape", "circle") == key)
        act.triggered.connect(_make_shape_switch(key))
        shape_actions[key] = act
        shape_menu.addAction(act)

    # 方向增益子菜单
    gain_menu = menu.addMenu("方向增益(准星)")
    gain_actions = {}
    GAIN_VALUES = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0]
    def _make_gain_switch(val):
        def _switch():
            CONFIG["direction_gain"] = val
            for v, act in gain_actions.items():
                act.setChecked(abs(v - val) < 0.01)
        return _switch
    for val in GAIN_VALUES:
        act = QAction(f"{val:.1f}", gain_menu, checkable=True)
        act.setChecked(abs(CONFIG.get("direction_gain", 1.0) - val) < 0.01)
        act.triggered.connect(_make_gain_switch(val))
        gain_actions[val] = act
        gain_menu.addAction(act)

    # 中心点大小
    center_menu = menu.addMenu("中心点大小")
    center_actions = {}
    DOT_SIZES = [1, 2, 3, 4, 5, 6, 8]
    def _make_dot_switch(val):
        def _switch():
            CONFIG["center_dot_size"] = val
            radar.update()
            for v, act in center_actions.items():
                act.setChecked(v == val)
        return _switch
    for val in DOT_SIZES:
        act = QAction(f"{val} px", center_menu, checkable=True)
        act.setChecked(CONFIG.get("center_dot_size", 4) == val)
        act.triggered.connect(_make_dot_switch(val))
        center_actions[val] = act
        center_menu.addAction(act)

    # 准星样式
    crosshair_menu = menu.addMenu("准星样式")
    crosshair_actions = {}
    def _make_crosshair_switch(key):
        def _switch():
            CONFIG["crosshair_style"] = key
            radar.update()
            for k, act in crosshair_actions.items():
                act.setChecked(k == key)
        return _switch
    for key, name in CROSSHAIR_NAMES.items():
        act = QAction(name, crosshair_menu, checkable=True)
        act.setChecked(CONFIG.get("crosshair_style", "cross") == key)
        act.triggered.connect(_make_crosshair_switch(key))
        crosshair_actions[key] = act
        crosshair_menu.addAction(act)

    # 重启音频
    act_restart = QAction("重启音频捕获", menu)
    act_restart.triggered.connect(lambda: radar.restart_audio())
    menu.addAction(act_restart)

    act_settings = QAction("设置...", menu)
    def _open_settings():
        dlg = SettingsDialog(analyzer=radar.analyzer)
        if dlg.exec_():
            # 如果设备变了，重启音频
            radar.restart_audio()
    act_settings.triggered.connect(_open_settings)
    menu.addAction(act_settings)

    menu.addSeparator()

    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
