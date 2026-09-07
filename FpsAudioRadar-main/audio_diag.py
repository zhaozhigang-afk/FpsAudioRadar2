#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频设备诊断脚本：检测声道、采样率、WASAPI回环能力"""

import pyaudiowpatch as pyaudio

p = pyaudio.PyAudio()

print("=" * 60)
print("  音频设备诊断报告")
print("=" * 60)

# 主机API
print("\n【主机API列表】")
for i in range(p.get_host_api_count()):
    info = p.get_host_api_info_by_index(i)
    print(f"  API[{i}] {info['name']}  type={info['type']}  设备数={info['deviceCount']}")

# WASAPI 默认输出
print("\n【WASAPI 默认输出设备】")
try:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    idx = wasapi['defaultOutputDevice']
    dev = p.get_device_info_by_index(idx)
    print(f"  名称:       {dev['name']}")
    print(f"  索引:       {idx}")
    print(f"  输入通道:   {dev.get('maxInputChannels', 0)}")
    print(f"  输出通道:   {dev.get('maxOutputChannels', 0)}")
    print(f"  采样率:     {dev['defaultSampleRate']} Hz")
    print(f"  低延迟:     {dev.get('defaultLowOutputLatency', 'N/A')}")
    print(f"  高延迟:     {dev.get('defaultHighOutputLatency', 'N/A')}")

    ch = dev.get('maxOutputChannels', 0)
    if ch >= 8:
        print(f"  声道配置:   7.1 环绕 (8声道)")
    elif ch >= 6:
        print(f"  声道配置:   5.1 环绕 (6声道)")
    elif ch >= 4:
        print(f"  声道配置:   四声道 (4声道)")
    elif ch >= 2:
        print(f"  声道配置:   立体声 (2声道)")
    else:
        print(f"  声道配置:   单声道/异常")
except Exception as e:
    print(f"  错误: {e}")

# 全部设备
print("\n【全部音频设备】")
for i in range(p.get_device_count()):
    dev = p.get_device_info_by_index(i)
    api = p.get_host_api_info_by_index(dev['hostApi'])
    is_loopback = 'loopback' in dev['name'].lower()
    tag = " [LOOPBACK]" if is_loopback else ""
    print(f"  [{i}] {dev['name']}{tag}")
    print(f"      API={api['name']}  in={dev.get('maxInputChannels',0)}  out={dev.get('maxOutputChannels',0)}  rate={dev['defaultSampleRate']}")

# 测试回环打开
print("\n【WASAPI 回环打开测试】")
try:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    idx = wasapi['defaultOutputDevice']
    dev = p.get_device_info_by_index(idx)
    ch = max(2, dev.get('maxOutputChannels', 2))
    rate = int(dev['defaultSampleRate'])
    stream = p.open(
        format=pyaudio.paInt16,
        channels=ch,
        rate=rate,
        frames_per_buffer=2048,
        input=True,
        input_device_index=idx,
        as_loopback=True,
    )
    print(f"  回环打开成功! 通道={ch}, 采样率={rate}")
    stream.stop_stream()
    stream.close()
except Exception as e:
    print(f"  回环打开失败: {e}")

p.terminate()
print("\n" + "=" * 60)
print("  诊断完成")
print("=" * 60)
