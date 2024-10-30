# ffmpeg-audio-splitter
> 基于 FFmpeg 命令行调用的低响度音频并行分割处理脚本

## 功能
* 基于 FFmpeg 命令行调用, 对输入音频文件进行低响度音频并行分割处理, 并输出到指定目录 (或合并至指定文件)
* 智能分析音视频轨道并提供选择
* AsyncIO 并行处理, 完全利用硬件性能
* 任务自动分配机制, 自动分配使得各进程负担均衡

## 安装
### Release 版本
下载 Release 版本的 ffmpeg-audio-splitter 脚本 (若有)

### 源代码版本
1. 克隆本仓库到本地
2. 安装 FFmpeg (<https://ffmpeg.org/download.html>)
3. 使用 Python 3.8 <= x <= 3.12 运行 `ffmpeg_audio_filter/main.py` (无需安装第三方依赖, 部分 Linux 最小安装的 Python 可能缺失依赖, 请自行安装)

## 编译打包
1. 安装 Nuitka (推荐在虚拟环境中安装)
```shell
pip install nuitka
```
2. 安装 gcc 或 clang 或 MSVC (已安装并添加至 PATH 环境变量者请忽略)
3. 在当前 Python 环境下运行 `build.bat` (Windows) 或 `build.sh`(若有) (*nix)
4. 待编译完成方可

## 使用
### 参数
```
usage: ffmpeg-audio-splitter [-h] -o OUTPUT [-os OUTPUT_SUFFIX] -s SILENCE_DB -sd SILENCE_DURATION [-a AUDIO_INDEX] [-p PROCESSES] [-m] [-np] input

split audio by silence

positional arguments:  # 位置参数
  input                 input file  # 输入的文件 (FFmpeg 支持的格式)

options:  # 键值参数
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT  # 输出目录或文件 (当 `--merge-parts` 时, 须为文件, 否则为目录)
                        output file
  -os OUTPUT_SUFFIX, --output-suffix OUTPUT_SUFFIX  # 输出文件后缀 ("auto" 则使用自动匹配, 部分冷门编码需要手动指定文件后缀)
                        output file suffix ("auto" to use "codec_name")
  -s SILENCE_DB, --silence-db SILENCE_DB  # 响度阈值 (dB), 低于该响度会被分割, 参见 https://ffmpeg.org/ffmpeg-filters.html#silencedetect
                        silence threshold in dB
  -sd SILENCE_DURATION, --silence-duration SILENCE_DURATION  # 响度持续时间 (秒), 超过该时间后才会被分割, 参见 https://ffmpeg.org/ffmpeg-filters.html#silencedetect
                        silence time in seconds
  -a AUDIO_INDEX, --audio-index AUDIO_INDEX  # 音频流索引 (从 0 开始), 对于有多条音轨的媒体必须指定 (也可在运行时交互选择)
                        audio stream index (from 0 to start)
  -p PROCESSES, --processes PROCESSES  # 并行处理进程数 (默认为 1/2 CPU逻辑核心数 (小于 1 时为 1), 无法获取CPU逻辑核心数时则为 4)
                        number of processes
  -mp, --merge-parts     merge parts into one file  # 启用后将合并分割后的音频文件 (需指定输出文件为文件)
  -np, --non-print-progress  # 启用后将不打印 FFmpeg 进度 (将标准输出重定向到 DEVNULL)
                        not print ffmpeg progress or yes

by lovemilk
```

### Example
将 `input.mkv` 音频文件中低于 -30dB 响度且持续时间超过 1s 的音频段丢弃, 并将分割后的音频段合并至 `output/merged.aac` (若音频以 aac 格式编码) 文件中.
```
ffmpeg-audio-splitter input.mkv -o output/merged -s -30 -sd 1 -a 0 -mp
```

将 `input.mkv` 音频文件中低于 -30dB 响度且持续时间超过 1s 的音频段丢弃, 并将分割后的各音频保存至 `output/` 文件夹
```
ffmpeg-audio-splitter input.mkv -o output/ -s -30 -sd 1 -a 0
```
