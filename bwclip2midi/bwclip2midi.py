#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bwclip2midi — Bitwig Studio .bwclip → 标准 MIDI (.mid) 转换工具
===============================================================

用法:
    python bwclip2midi.py <input.bwclip> [output.mid]
    python bwclip2midi.py <folder>              # 批量转换目录下所有 .bwclip
    python bwclip2midi.py <input.bwclip> --ppq 480

无需第三方依赖，纯 Python 3。

--- .bwclip 格式说明（通过对 bitwig.jar 6.1.3 逆向得到）---

文件布局:
    [42 字节 ASCII 头]  "BtWg" + 38 个 hex 字符:
        4 位: 文件格式版本 (3)
        4 位: 序列化格式 ID (2 = 二进制)
        4 位: 文档版本 (转换器数量, 如 198)
        8 位: 正文起始偏移 (= 头 + meta + 5000 空格 + '\\n' 之后)
        18 位: 0
    [meta 对象]  名称/作者/BPM 等元数据 (可就地改写)
    [5000 个空格 + '\\n']  预留区 (供 Bitwig 就地改写 meta)
    [文档主体]  文档对象树

二进制语法 (大端):
    int8/int16/int32/int64, float32, float64
    字符串   = int32(长度 | 0x80000000=UTF-16) + 字符 (UTF-16 时每字符 2 字节)
    对象     = int32(类ID) + 属性* + int32(0)
               类ID==4 表示后跟字符串类名 (如根节点 "meta")
    属性     = int32(属性ID) + 1 字节(类型) + 值
               属性ID==1 表示后跟字符串属性名 (如 meta 里的 "bpm")
    类型字节: 1=int8 2=int16 3=int32 4=int64 5=bool 6=float 7=double
              8=string 9=嵌套对象 10=null 11=对象引用(int32 id)
              12=外部引用(字符串) 13..17=数组 18=对象列表 20=字符串映射
              21=UUID(16字节) 22=颜色(4×float RGBA) 23..25=数组 26=相对引用
    对象列表 = int32(元素类型) + 元素* ; 元素类型 3 = 列表结束

音符数据位于文档树中的 "音高通道" 对象 (类ID 66):
    #238 = MIDI 音高 (0..127)
    #543 = 该音高上的音符列表 (类ID 102):
        #687 = 起始位置 (拍, 1 拍 = 四分音符)
        #38  = 时长 (拍)
        #239 = 力度 (0..1, MIDI 力度 = round(v × 127))
BPM 在文档根 (类ID 479) 的 #5925 属性 (double)。

自动化 (pitchbend / CC / 通道压力) 存于自动化容器 (类ID 4002) 的通道列表:
    class 573  自动化通道
      #675.#4922 = 参数显示名, 如 "Ch. 1 Pitch Bend" / "Ch. 1 Expression (#11)"
      #11661 → class 3650 → #561 → class 3638 → #576 = 区段 (class 3472):
          #687 = 区段在 clip 内的起点 (拍), #38 = 区段长度
          #10985 → class 3772 → #4329 (class 60) → #1166 = 曲线 (class 110)
      曲线点 (class 264): #687 = 位置 (拍, 相对区段), #655 = 值
          pitchbend 值域 -1..1 (0 = 居中), CC/压力值域 0..1
"""

import argparse
import os
import re
import struct
import sys

# --------------------------------------------------------------------------
# BtWg 二进制解析器
# --------------------------------------------------------------------------

HEADER_SIZE = 0x2A
RESERVED_SPACE = 5000  # sYG.Dvd(): meta 改写预留的字节数 (空格 + '\n')

TYPE_NAMES = {
    1: 'int8', 2: 'int16', 3: 'int32', 4: 'int64', 5: 'bool', 6: 'float',
    7: 'double', 8: 'string', 9: 'object', 10: 'null', 11: 'object_ref',
    12: 'ext_object_ref', 13: 'byte[]', 14: 'int16[]', 15: 'int32[]',
    16: 'int64[]', 17: 'bool[]', 18: 'object_list', 20: 'map<string,object>',
    21: 'uuid', 22: 'color', 23: 'float[]', 24: 'double[]', 25: 'string[]',
    26: 'relative_ref',
}

# 文档模型中与 MIDI 转换相关的类/属性 ID (bitwig.jar 6.1.3, 文档版本 198)
CLASS_CLIP_ROOT = 479        # 文档根
CLASS_CLIP = 46              # Clip 本体 (含名称 #68)
CLASS_PITCH_LANE = 66        # 音高通道
CLASS_NOTE = 102             # 音符事件
CLASS_AUTO_CONTAINER = 4002  # 自动化容器 (#1210 = 通道列表)
CLASS_AUTO_CHANNEL = 573     # 自动化通道
CLASS_AUTO_REGION = 3472     # 自动化区段 (#687 起点, #38 长度, 拍)
CLASS_AUTO_CURVE = 110       # 自动化曲线 (#543 = 点列表)
CLASS_AUTO_POINT = 264       # 自动化点
PROP_CLIP = '#2409'          # 根 → Clip 对象
PROP_CLIP_NAME = '#68'       # Clip 名称
PROP_LANE_PITCH = '#238'     # 音高通道 → MIDI 音高
PROP_LANE_NOTES = '#543'     # 音高通道 → 音符列表
PROP_NOTE_START = '#687'     # 音符 → 起始 (拍)
PROP_NOTE_DUR = '#38'        # 音符 → 时长 (拍)
PROP_NOTE_VEL = '#239'       # 音符 → 力度 (0..1)
PROP_BPM = '#5925'           # 根 → BPM (double)
PROP_BEAT_LENGTH = '#5924'   # 根 → 每小节拍数 (double, 默认 4.0 = x/4 拍号)
PROP_AUTO_CHANNELS = '#1210' # 自动化容器 → 通道列表
PROP_AUTO_NAME = '#675'      # 自动化通道 → 参数名对象 (class 241)
PROP_AUTO_TITLE = '#4922'    # 参数名对象 → 显示名, 如 "Ch. 1 Expression (#11)"
PROP_AUTO_REGIONS = '#11661' # 自动化通道 → 区段包装 (class 3650)
PROP_REGION_LIST = '#561'    # 区段包装 → 区段列表 (class 3638)
PROP_REGION = '#576'         # 区段条目 → 区段 (class 3472)
PROP_REGION_CURVES = '#10985'# 区段 → 曲线包装 (class 3772)
PROP_REGION_LANE = '#4329'   # 曲线包装 → 通道 (class 60)
PROP_LANE_CURVE = '#1166'    # 通道 → 曲线 (class 110)
PROP_CURVE_POINTS = '#543'   # 曲线 → 点列表 (class 264)
PROP_POINT_POS = '#687'      # 自动化点 → 位置 (拍, 相对区段)
PROP_POINT_VAL = '#655'      # 自动化点 → 值 (pitchbend: -1..1, CC: 0..1)


class BwClipError(Exception):
    pass


class Reader:
    __slots__ = ('d', 'p')

    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def byte(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def i16(self):
        v = struct.unpack_from('>h', self.d, self.p)[0]
        self.p += 2
        return v

    def i32(self):
        v = struct.unpack_from('>i', self.d, self.p)[0]
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from('>q', self.d, self.p)[0]
        self.p += 8
        return v

    def f32(self):
        v = struct.unpack_from('>f', self.d, self.p)[0]
        self.p += 4
        return v

    def f64(self):
        v = struct.unpack_from('>d', self.d, self.p)[0]
        self.p += 8
        return v

    def string(self):
        n = self.i32()
        if n & 0x80000000:
            n &= 0x7FFFFFFF
            s = self.d[self.p:self.p + n * 2].decode('utf-16-be')
            self.p += n * 2
        else:
            s = self.d[self.p:self.p + n].decode('latin-1')
            self.p += n
        return s

    def uuid(self):
        self.p += 16
        return None


class Parser:
    """按 bitwig.jar 中 com/bitwig/base/serial/binary 的语法解析对象树。"""

    def __init__(self, data, pos=0):
        self.r = Reader(data, pos)

    def read_object(self):
        r = self.r
        n = r.i32()
        cls = r.string() if n == 4 else n
        obj = {'$class': cls, '$props': {}}
        header = r.i32()
        while header != 0:
            name = r.string() if header == 1 else '#%d' % header
            t = r.byte()
            obj['$props'][name] = self.read_value(t)
            header = r.i32()
        return obj

    def read_value(self, t):
        r = self.r
        if t == 1:
            v = r.byte()
            return v - 256 if v > 127 else v
        if t == 2:
            return r.i16()
        if t == 3:
            return r.i32()
        if t == 4:
            return r.i64()
        if t == 5:
            return bool(r.byte())
        if t == 6:
            return r.f32()
        if t == 7:
            return r.f64()
        if t == 8:
            return r.string()
        if t == 9:
            return self.read_object()
        if t == 10:
            return None
        if t == 11:
            return {'$ref': r.i32()}
        if t == 12:
            return {'$extref': r.string()}
        if t == 13:
            n = r.i32()
            v = r.d[r.p:r.p + n]
            r.p += n
            return v
        if t == 14:
            n = r.i32()
            v = [r.i16() for _ in range(n)]
            return v
        if t == 15:
            n = r.i32()
            return [r.i32() for _ in range(n)]
        if t == 16:
            n = r.i32()
            return [r.i64() for _ in range(n)]
        if t == 17:
            n = r.i32()
            return [bool(r.byte()) for _ in range(n)]
        if t == 18:
            return self.read_object_list()
        if t == 20:
            return self.read_map()
        if t == 21:
            r.uuid()
            return None
        if t == 22:
            return [r.f32() for _ in range(4)]
        if t == 23:
            n = r.i32()
            return [r.f32() for _ in range(n)]
        if t == 24:
            n = r.i32()
            return [r.f64() for _ in range(n)]
        if t == 25:
            n = r.i32()
            return [r.string() for _ in range(n)]
        if t == 26:
            a = r.i32()
            ref = {'$relref_class': a}
            if a == 1:
                ref['$relref_id'] = r.i32()
            else:
                ref['$relref_obj'] = self.read_object_with_class(a)
            ref['$relref_name'] = r.string()
            return ref
        raise BwClipError('偏移 %#x: 未知类型字节 %d' % (r.p - 1, t))

    def read_object_with_class(self, cls):
        r = self.r
        obj = {'$class': cls, '$props': {}}
        header = r.i32()
        while header != 0:
            name = r.string() if header == 1 else '#%d' % header
            t = r.byte()
            obj['$props'][name] = self.read_value(t)
            header = r.i32()
        return obj

    def read_object_list(self):
        r = self.r
        kind = r.i32()
        out = []
        while kind != 3:
            if kind == 0:
                kind = r.i32()
                out.append(None)
            elif kind == 1:
                out.append({'$ref': r.i32()})
                kind = r.i32()
            elif kind == 2:
                out.append({'$extref': r.string()})
                kind = r.i32()
            elif kind == 5:
                out.append({'$extref': r.string()})
                kind = r.i32()
            else:
                out.append(self.read_object_with_class(kind))
                kind = r.i32()
        return out

    def read_map(self):
        r = self.r
        flag = r.byte()
        out = {}
        while flag != 0:
            key = r.string()
            t = r.byte()
            out[key] = self.read_value(t)
            flag = r.byte()
        return out


def parse_bwclip(path):
    """返回 (header_dict, meta_obj, content_obj)。"""
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < HEADER_SIZE or data[:4] != b'BtWg':
        raise BwClipError('%s: 不是 Bitwig BtWg 格式文件' % path)
    h = data[4:HEADER_SIZE].decode('ascii')
    try:
        header = {
            'format_version': int(h[0:4], 16),
            'serialization_format': int(h[4:8], 16),
            'document_version': int(h[8:12], 16),
            'content_offset': int(h[12:20], 16),
        }
    except ValueError:
        raise BwClipError('%s: 文件头损坏' % path)
    if header['serialization_format'] != 2:
        raise BwClipError('%s: 不支持的序列化格式 %d (仅支持二进制格式 2)'
                          % (path, header['serialization_format']))
    p = Parser(data, HEADER_SIZE)
    meta = p.read_object()
    # 跳过预留区 (5000 空格 + '\n'), 与 sYG 的读取逻辑一致
    while p.r.p < len(data) and data[p.r.p] == 0x20:
        p.r.p += 1
    if p.r.p >= len(data) or data[p.r.p] != 0x0A:
        raise BwClipError('%s: meta 后未找到预留区结束符' % path)
    p.r.p += 1
    if p.r.p != header['content_offset']:
        raise BwClipError('%s: 偏移不符 (实际 %#x, 头部声明 %#x)'
                          % (path, p.r.p, header['content_offset']))
    content = p.read_object()
    return header, meta, content


# --------------------------------------------------------------------------
# 音符提取
# --------------------------------------------------------------------------

def iter_objects(node):
    """深度优先遍历树中的所有对象 (含列表、相对引用内部)。"""
    if isinstance(node, dict):
        if '$class' in node:
            yield node
            for v in node['$props'].values():
                yield from iter_objects(v)
        else:
            for v in node.values():
                yield from iter_objects(v)
    elif isinstance(node, list):
        for e in node:
            yield from iter_objects(e)


def extract_midi_data(content):
    """返回 dict: {bpm, beats_per_bar, name, notes, controllers}。

    notes:       [(start_beats, dur_beats, velocity, pitch)]
    controllers: [(kind, channel, points)]
                 kind: 'pb' | ('cc', n) | 'pressure'
                 points: [(abs_pos_beats, value)]
    """
    if content.get('$class') != CLASS_CLIP_ROOT:
        raise BwClipError('文档根不是 Clip (类ID %s)' % content.get('$class'))
    props = content['$props']
    bpm = props.get(PROP_BPM)
    beats_per_bar = props.get(PROP_BEAT_LENGTH) or 4.0
    clip = props.get(PROP_CLIP)
    name = ''
    if isinstance(clip, dict):
        name = clip['$props'].get(PROP_CLIP_NAME) or ''
    notes = []
    for obj in iter_objects(content):
        if obj['$class'] != CLASS_PITCH_LANE:
            continue
        pitch = obj['$props'].get(PROP_LANE_PITCH)
        if not isinstance(pitch, int) or not 0 <= pitch <= 127:
            continue
        for ev in obj['$props'].get(PROP_LANE_NOTES) or []:
            if not (isinstance(ev, dict) and ev.get('$class') == CLASS_NOTE):
                continue
            ep = ev['$props']
            start = ep.get(PROP_NOTE_START)
            dur = ep.get(PROP_NOTE_DUR)
            vel = ep.get(PROP_NOTE_VEL)
            if start is None or dur is None or vel is None:
                continue
            notes.append((float(start), float(dur), vel, pitch))
    controllers = []
    for obj in iter_objects(content):
        if obj['$class'] != CLASS_AUTO_CHANNEL:
            continue
        parsed = _parse_auto_channel(obj)
        if parsed:
            controllers.append(parsed)
    return {
        'bpm': float(bpm) if bpm else 120.0,
        'beats_per_bar': float(beats_per_bar),
        'name': name,
        'notes': notes,
        'controllers': controllers,
    }


def _curve_points(curve_obj):
    """class 110 曲线 → [(pos_beats, value)]。"""
    pts = []
    for p in (curve_obj or {}).get('$props', {}).get(PROP_CURVE_POINTS) or []:
        if not (isinstance(p, dict) and p.get('$class') == CLASS_AUTO_POINT):
            continue
        pp = p['$props']
        pos, val = pp.get(PROP_POINT_POS), pp.get(PROP_POINT_VAL)
        if pos is None or val is None:
            continue
        pts.append((float(pos), float(val)))
    return pts


def _parse_auto_channel(channel_obj):
    """class 573 自动化通道 → (kind, channel, points) 或 None。

    类型来自显示名 (Bitwig 用英文名标识 MIDI 自动化目标):
        "Ch. 1 Pitch Bend"            → pitchbend, 值域 -1..1
        "Ch. 1 Expression (#11)"      → CC #11, 值域 0..1
        "Ch. 1 Aftertouch"/"Pressure" → 通道压力, 值域 0..1
    """
    nameobj = channel_obj['$props'].get(PROP_AUTO_NAME)
    title = (nameobj or {}).get('$props', {}).get(PROP_AUTO_TITLE) or ''
    low = title.lower()
    midi_ch = 0
    m = re.search(r'ch\.?\s*(\d+)', low)
    if m:
        midi_ch = min(15, max(0, int(m.group(1)) - 1))
    if 'pitch' in low and 'bend' in low:
        kind = 'pb'
    elif 'aftertouch' in low or ('pressure' in low and '(#' not in low):
        kind = 'pressure'
    else:
        m = re.search(r'\(#(\d+)\)', title)
        if not m:
            return None
        kind = ('cc', min(127, max(0, int(m.group(1)))))
    # 收集所有区段曲线的点 (位置换算为 clip 内绝对拍)
    points = []
    regions = channel_obj['$props'].get(PROP_AUTO_REGIONS) or {}
    items = regions.get('$props', {}).get(PROP_REGION_LIST) or []
    for item in items:
        region = (item or {}).get('$props', {}).get(PROP_REGION)
        if not isinstance(region, dict):
            continue
        rp = region['$props']
        base = rp.get(PROP_NOTE_START) or 0.0  # 区段在 clip 内的起点 (#687)
        lane = ((rp.get(PROP_REGION_CURVES) or {}).get('$props', {})
                .get(PROP_REGION_LANE))
        curve = (lane or {}).get('$props', {}).get(PROP_LANE_CURVE)
        for pos, val in _curve_points(curve):
            points.append((base + pos, val))
    if not points:
        # 无区段时退回通道级全局曲线 (#11660 → class 3473 → #13740)
        glob = channel_obj['$props'].get('#11660')
        curve = (glob or {}).get('$props', {}).get('#13740')
        points = _curve_points(curve)
    points.sort()
    return (kind, midi_ch, points)


# --------------------------------------------------------------------------
# 标准 MIDI 文件 (SMF) 写出
# --------------------------------------------------------------------------

def _varlen(n):
    """MIDI 变长数量值编码。"""
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _chunk(tag, payload):
    return tag + struct.pack('>I', len(payload)) + payload


def _resample_curve(points, step):
    """沿控制点折线按 step(拍) 密集采样, 保证输出为连贯折线。

    每段先输出起点再输出段内插值点, 最后输出终点, 因此同一 tick 上
    真实控制点会覆盖插值点 (控制点优先)。
    """
    if len(points) < 2 or step <= 0:
        return list(points)
    out = []
    for (p1, v1), (p2, v2) in zip(points, points[1:]):
        out.append((p1, v1))
        span = p2 - p1
        if span > step:
            n = int(span / step)
            for i in range(1, n + 1):
                q = p1 + i * step
                if q >= p2:
                    break
                out.append((q, v1 + (v2 - v1) * (i * step / span)))
    out.append(points[-1])
    return out


DEFAULT_INTERP_STEP = 1.0 / 48.0  # 1/48 拍: 960 PPQ 下每 20 tick 一个事件


def write_midi(path, midi_data, ppq=960, track_name=True,
               interp_step=DEFAULT_INTERP_STEP):
    bpm = midi_data['bpm']
    bpb = midi_data['beats_per_bar']
    notes = midi_data['notes']
    name = midi_data['name']
    controllers = midi_data.get('controllers') or []

    # 音符转 tick
    events = []  # (tick, sort_order, bytes)
    for start, dur, vel, pitch in notes:
        on_tick = max(0, int(round(start * ppq)))
        off_tick = max(on_tick + 1, int(round((start + dur) * ppq)))
        velocity = min(127, max(1, int(round(vel * 127))))
        events.append((on_tick, 1, bytes((0x90, pitch, velocity))))
        events.append((off_tick, 0, bytes((0x80, pitch, 0))))

    # 自动化 → MIDI 事件
    #   pitch bend:  值域 -1..1 → 14 位 0..16383, 0 → 8192 (居中)
    #   CC/压力:      值域 0..1 → 7 位 0..127
    # 沿控制点折线密集采样 (MIDI 控制器是"保持"语义, 采样越密越接近
    # 原曲线), 同一 tick 的重复事件只保留最后一个 (控制点优先)。
    for kind, ch, points in controllers:
        events_one = {}
        for pos, val in _resample_curve(points, interp_step):
            tick = max(0, int(round(pos * ppq)))
            if kind == 'pb':
                v14 = int(round((val + 1.0) / 2.0 * 16383))
                v14 = min(16383, max(0, v14))
                data = bytes((0xE0 | ch, v14 & 0x7F, (v14 >> 7) & 0x7F))
            elif kind == 'pressure':
                v7 = min(127, max(0, int(round(val * 127))))
                data = bytes((0xD0 | ch, v7))
            else:
                v7 = min(127, max(0, int(round(val * 127))))
                data = bytes((0xB0 | ch, kind[1], v7))
            events_one[tick] = data
        for tick, data in sorted(events_one.items()):
            events.append((tick, 2, data))
    events.sort(key=lambda e: (e[0], e[1]))

    track = bytearray()
    if track_name:
        raw = name.encode('utf-8') if name else b'Clip'
        track += b'\x00\xff\x03' + _varlen(len(raw)) + raw
    # 拍号: 分子 = 每小节拍数, 分母固定 4 (Bitwig 1 拍 = 四分音符)
    ts_num = min(255, max(1, int(round(bpb))))
    track += b'\x00\xff\x58\x04' + bytes((ts_num, 2, 24, 8))
    # 速度
    mpqn = int(round(60_000_000.0 / bpm))
    track += b'\x00\xff\x51\x03' + struct.pack('>I', mpqn)[1:]
    # 音符事件 (delta time)
    last = 0
    for tick, _, data in events:
        track += _varlen(tick - last) + data
        last = tick
    track += b'\x00\xff\x2f\x00'  # EOT

    smf = _chunk(b'MThd', struct.pack('>HHH', 0, 1, ppq))
    smf += _chunk(b'MTrk', bytes(track))
    with open(path, 'wb') as f:
        f.write(smf)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------

def convert_one(src, dst, ppq, interp_step=DEFAULT_INTERP_STEP):
    header, meta, content = parse_bwclip(src)
    midi_data = extract_midi_data(content)
    write_midi(dst, midi_data, ppq=ppq, interp_step=interp_step)
    n = len(midi_data['notes'])
    ctrl = midi_data.get('controllers') or []
    desc = ', '.join(
        '%s ch%d (%d 点)' % (
            'PitchBend' if k == 'pb' else
            '压力' if k == 'pressure' else 'CC#%d' % k[1],
            ch + 1, len(pts))
        for k, ch, pts in ctrl)
    print('%s -> %s  (%d 个音符%s, BPM %.4g, 名称: %s)'
          % (os.path.basename(src), os.path.basename(dst), n,
             ('; ' + desc) if ctrl else '',
             midi_data['bpm'], midi_data['name'] or '<无>'))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Bitwig .bwclip → 标准 MIDI 转换工具')
    ap.add_argument('input', help='.bwclip 文件或包含它们的目录')
    ap.add_argument('output', nargs='?', help='输出 .mid 路径 (默认同名 .mid)')
    ap.add_argument('--ppq', type=int, default=960,
                    help='MIDI 分辨率 (每四分音符 tick 数, 默认 960)')
    ap.add_argument('--interp-step', type=float, default=DEFAULT_INTERP_STEP,
                    metavar='BEATS',
                    help='自动化折线采样步长 (拍, 默认 1/48 ≈ 0.0208; '
                         '越小越平滑, 0 = 只输出控制点)')
    ns = ap.parse_args(argv)

    if os.path.isdir(ns.input):
        files = sorted(f for f in os.listdir(ns.input)
                       if f.lower().endswith('.bwclip'))
        if not files:
            print('目录中没有 .bwclip 文件: %s' % ns.input, file=sys.stderr)
            return 1
        outdir = ns.output or ns.input
        os.makedirs(outdir, exist_ok=True)
        rc = 0
        for f in files:
            src = os.path.join(ns.input, f)
            dst = os.path.join(outdir, os.path.splitext(f)[0] + '.mid')
            try:
                convert_one(src, dst, ns.ppq, ns.interp_step)
            except Exception as e:
                print('%s: %s' % (f, e), file=sys.stderr)
                rc = 1
        return rc

    if not os.path.isfile(ns.input):
        print('文件不存在: %s' % ns.input, file=sys.stderr)
        return 1
    dst = ns.output or os.path.splitext(ns.input)[0] + '.mid'
    try:
        convert_one(ns.input, dst, ns.ppq, ns.interp_step)
    except Exception as e:
        print('错误: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
