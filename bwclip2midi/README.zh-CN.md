# bwclip2midi — Bitwig Studio .bwclip → 标准 MIDI 文件转换工具

[English](README.md) | 简体中文

把从 Bitwig Studio 拖出的 MIDI Clip（`.bwclip`）转换成通用标准 MIDI 文件
（`.mid`），可在任何 DAW / 音序器中打开。

## 用法

```bash
# 单文件（在输入旁生成同名 .mid）
python bwclip2midi.py 路径/clip.bwclip

# 指定输出路径
python bwclip2midi.py clip.bwclip my.mid

# 批量转换整个目录
python bwclip2midi.py bwclip_sample output

# 修改 MIDI 分辨率（默认 960 PPQ）
python bwclip2midi.py clip.bwclip --ppq 480

# 自动化折线采样步长（拍；默认 1/48，0 = 只输出控制点）
python bwclip2midi.py clip.bwclip --interp-step 0.25
```

Windows 下也可以直接把 `.bwclip` 文件（或整个文件夹）拖到
`bwclip2midi.bat` 上。

无需任何第三方依赖——纯 Python 3 标准库。

## 转换内容

| bwclip 数据 | MIDI 输出 |
|---|---|
| 音符（音高 / 起始 / 时长 / 力度） | Note On / Note Off |
| Pitch Bend 自动化（值域 -1..1） | Pitch Bend（14-bit，0 → 居中 8192） |
| CC 自动化（如 Expression #11，值域 0..1） | Control Change（7-bit） |
| Aftertouch / Pressure 自动化 | Channel Pressure |
| BPM（文档根属性） | Set Tempo 元事件 |
| 每小节拍数（默认 4） | Time Signature 4/4 元事件 |
| Clip 名称 | Track Name 元事件 |

- 分辨率默认 960 ticks / 四分音符；位置按“1 拍 = 四分音符”换算。
- 力度按 `round(velocity × 127)` 换算并夹取到 1..127。
- 时长不足 1 tick 的音符夹取为 1 tick，保证 Note Off 不会早于 Note On。
- 自动化曲线以密集折线输出：保留全部源控制点，段内按 `--interp-step`
  （默认 1/48 拍 ≈ 0.0208，即 960 PPQ 下每 20 ticks）采样，导入任何 DAW
  后都是一条连贯曲线。设 `--interp-step 0` 可只输出控制点。
- 输出 SMF format 0（单轨），音符与控制器事件按时间合并。

### 已知限制

- Bitwig 的贝塞尔曲线控制柄按线性段近似（全部控制点均保留）。
- 按音符的 MPE 表达（per-note pressure/timbre）不做转换——标准 MIDI
  单轨文件无法表达按音符的控制器。
- 自动化目标通过 Bitwig 参数显示名识别（`"Ch. N Pitch Bend"`、
  `"Expression (#11)"` 等）。设备参数自动化（音量包络等）不是 MIDI
  数据，不做转换。

## .bwclip 格式（逆向结果）

通过对 `Bitwig Studio\bin\bitwig.jar`（6.1.3，文档版本 198）反编译得出。

### 文件布局

```
"BtWg" + 38 个 ASCII hex 字符
  ├─ 4 位: 文件格式版本 (3)
  ├─ 4 位: 序列化格式 ID (2 = 二进制)
  ├─ 4 位: 文档版本 = 注册的转换器数量 (198)
  └─ 8 位: 文档主体的绝对起始偏移
[meta 对象]          名称 / BPM / 应用版本 等元数据
[5000 个空格 + \n]   预留区（供 Bitwig 就地改写元数据）
[文档对象树]          真正的 Clip 内容
```

### 二进制语法（全部大端）

- 整数按能装下的最小类型存储：类型字节 1/2/3/4 对应 int8/int16/int32/int64。
- 字符串 = `int32(长度，最高位=1 表示 UTF-16)` + 字符（UTF-16 每字符 2
  字节，否则 1 字节）。
- 对象 = `int32 类ID` + 属性列表 + `int32 0` 结束标记。
  类ID 4 表示“后跟字符串类名”（如根对象名为 `"meta"`）。
- 属性 = `int32 属性ID` + 1 字节类型 + 值。
  属性ID 1 表示“后跟字符串属性名”。
- 类型字节：1 int8、2 int16、3 int32、4 int64、5 bool、6 float、7 double、
  8 字符串、9 嵌套对象、10 null、11 对象引用（int32）、12 外部引用（字符串）、
  13–17 数组、18 对象列表、20 字符串→对象映射、21 UUID（16 字节）、
  22 颜色（4×float RGBA）、23–25 数组、26 相对引用。
- 对象列表以元素类型 3 结束。

### 音符存储位置

音符位于“音高通道”对象（类ID 66）中：

```
#238 = MIDI 音高
#543 = 音符列表；每个音符（类ID 102）：
    #687 = 起始（拍）   #38 = 时长（拍）   #239 = 力度 (0..1)
```

BPM 在文档根（类ID 479）的 `#5925` 属性（double）。

### 自动化存储位置

自动化（pitchbend / CC / 压力）位于自动化容器（类ID 4002）的 `#1210`
通道列表中，每条通道是类ID 573 的对象：

```
#675.#4922 = 参数显示名，如 "Ch. 1 Pitch Bend"、"Ch. 1 Expression (#11)"
#11661 → class 3650 → #561 → class 3638 → #576 = 区段 (class 3472)
    区段 #687 = 在 clip 内的起点（拍）
    #10985 → class 3772 → #4329 (class 60) → #1166 = 曲线 (class 110)
    曲线点 (class 264)：#687 = 位置（拍，相对区段），#655 = 值
    pitchbend 值域 -1..1（0 居中）；CC / 压力值域 0..1
```

## 验证

样本 `1.bwclip`（16 个十六分音符的半音阶乐句）、`4.bwclip`（4 个四分
音符）和 `222.bwclip`（4 音符 + 83 点 Pitch Bend + 73 点 CC#11）均按上述
语法完整解析到文件末尾。转换结果用独立的 SMF 解析器回读比对：音符逐一
一致，每个控制器事件都落在源曲线对应位置的值域内（≤ 1 LSB 取整误差）。

## 仓库文件

- `bwclip2midi.py` — 转换工具（内含解析器，单文件）
- `bwclip2midi.bat` — Windows 拖放入口
