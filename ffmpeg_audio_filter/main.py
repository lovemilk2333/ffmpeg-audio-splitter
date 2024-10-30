import os
import sys
import asyncio
from re import search as re_search
from json import loads
from pathlib import Path
from asyncio import subprocess
from typing import Iterable, TypedDict, TypeAlias, Literal
from decimal import Decimal

CodecTypes: TypeAlias = Literal["audio", "video"]


class AudioStream(TypedDict):
    index: int
    codec_name: str
    codec_long_name: str
    profile: str
    codec_type: Literal["audio"]
    codec_tag_string: str
    codec_tag: str
    sample_fmt: str
    sample_rate: int
    channels: int
    channel_layout: str
    bits_per_sample: int
    initial_padding: int
    ts_packetsize: str
    id: str
    r_frame_rate: str
    avg_frame_rate: str
    time_base: str
    start_pts: int
    start_time: str
    duration_ts: int
    duration: str
    bit_rate: str
    disposition: dict


class VideoStream(TypedDict):
    index: int
    codec_name: str
    codec_long_name: str
    profile: str
    codec_type: Literal["video"]
    # 其他的用不到就不写类型了


class MediaInfo(TypedDict):
    streams: list[AudioStream | VideoStream]
    format: dict


StartTime: TypeAlias = Decimal
EndTime: TypeAlias = Decimal
Duration: TypeAlias = Decimal


class SilencesStruct(TypedDict):
    file: Path
    audio_index: int
    suffix: str
    silences: list[tuple[StartTime, EndTime, Duration]]


CODEC_NAME_MAPPING = {
    "aac": "aac",
    "libmp3lame": "mp3",
    "libvorbis": "ogg",
    "flac": "flac",
    "pcm_s16le": "wav",
    "opus": "opus",
    "libaom": "webm",
}


def get_suffix(codec_name: str, /, *, default: str = "{codec_name}-audio.aac") -> str:
    return CODEC_NAME_MAPPING.get(codec_name, default.format(codec_name=codec_name))


async def check_ffmpeg():
    process = await asyncio.create_subprocess_shell(
        "ffmpeg -version", stdout=subprocess.DEVNULL
    )
    assert await process.wait() == 0, "ffmpeg is not installed"

    process = await asyncio.create_subprocess_shell(
        "ffprobe -version", stdout=subprocess.DEVNULL
    )
    assert await process.wait() == 0, "ffprobe is not installed"


def get_audios(media_info: MediaInfo) -> Iterable[AudioStream]:
    return filter(lambda s: s["codec_type"] == "audio", media_info["streams"])  # type: ignore


async def get_media_info(file: str | Path, encoding: str = "u8") -> MediaInfo:
    file = Path(file).absolute().resolve()
    assert file.exists(), f"{file} does not exist"
    assert file.is_file(), f"{file} is not a file"

    command = (
        f'ffprobe -i "{file}" -v quiet -print_format json -show_format -show_streams'
    )
    process = await subprocess.create_subprocess_shell(command, stdout=subprocess.PIPE)
    stdout, _ = await process.communicate()
    return loads(stdout.decode(encoding))


async def get_silence(
    file: str | Path,
    *,
    silence_db: float,
    silence_duration: float,
    suffix: str,
    audio_index: int = 0,
    encoding: str = "u8",
) -> SilencesStruct:
    file = Path(file).absolute().resolve()
    assert file.exists(), f"{file} does not exist"
    assert file.is_file(), f"{file} is not a file"

    command = f'ffmpeg -i "{file}" -map a:{audio_index} -af "silencedetect=n={silence_db}dB:d={silence_duration}" -f null -'
    process = await subprocess.create_subprocess_shell(command, stderr=subprocess.PIPE)
    _, stderr = await process.communicate()

    silences = []
    for line in stderr.decode(encoding).splitlines():
        line = line.strip()
        if not line.startswith("[silencedetect @ "):
            continue
        elif "silence_start: " in line:
            continue

        end_match = re_search(r"silence_end: (\d+\.\d+)", line)
        duration_match = re_search(r"silence_duration: (\d+\.\d+)", line)
        if end_match and duration_match:
            # float 会精度丢失, 变成诸如 1922.3068130000001
            end = Decimal(end_match.group(1))
            duration = Decimal(duration_match.group(1))
            silences.append((end - duration, end, duration))

    return SilencesStruct(
        file=file, audio_index=audio_index, silences=silences, suffix=suffix
    )


async def _split_silence(
    silences: SilencesStruct,
    task_index: int,
    output_dir: Path,
    output_name: str,
    print_progress: bool = True,
) -> tuple[int, Path]:
    start, end, duration = silences["silences"][task_index]
    file = silences["file"]
    audio_index = silences["audio_index"]
    full_filename = (
        (
            output_dir
            / output_name.format(
                index=task_index,
                audio_index=audio_index,
                file=file,
                start=start,
                end=end,
                duration=duration,
                suffix=silences["suffix"],
            )
        )
        .absolute()
        .resolve()
    )
    command = f'ffmpeg -i "{silences["file"]}" -map a:{audio_index } -ss {start} -to {end} -c copy "{full_filename}" -y'
    process = await asyncio.create_subprocess_shell(
        command, stderr=None if print_progress else subprocess.DEVNULL
    )
    return await process.wait(), full_filename


async def split_silences(
    silence_tasks: SilencesStruct,
    task_range: Iterable[int],
    output_dir: Path,
    output_name: str = ".{file.stem}_{start}-{end}#{audio_index}_{index}.{suffix}",
    print_progress: bool = True,
):
    file = Path(silence_tasks["file"]).absolute().resolve()
    assert file.exists(), f"{file} does not exist"
    assert file.is_file(), f"{file} is not a file"

    result = []
    for i in task_range:
        result.append(
            await _split_silence(
                silence_tasks,
                task_index=i,
                output_dir=output_dir,
                output_name=output_name,
                print_progress=print_progress,
            )
        )
    return result


async def merge_parts_func(
    output_file: Path,
    filelist_file: Path,
    print_progress: bool = True,
):
    assert filelist_file.exists(), f"{filelist_file} does not exist"
    assert filelist_file.is_file(), f"{filelist_file} is not a file"

    command = (
        f'ffmpeg -f concat -safe 0 -i "{filelist_file}" -c copy "{output_file}" -y'
    )
    process = await asyncio.create_subprocess_shell(
        command, stderr=None if print_progress else subprocess.DEVNULL
    )
    return await process.wait()


def total_silence_duration(silences: SilencesStruct) -> Duration:
    return sum(duration for _, _, duration in silences["silences"])  # type: ignore


def assign_tasks(silences: SilencesStruct, max_process: int) -> list[Iterable[int]]:
    """
    分配任务
    分割 1s 消耗 1 cost, 开启一个 ffmpeg 消耗 3 cost
    """
    FFMPEG_COST: int = 3
    DURATION_COST_RATE: int | Decimal = 1
    REDUNDANCE_COST_RATE: int | Decimal = 2

    silences_count = len(silences["silences"])
    assert silences_count > 1, "silences cannot less than 2"

    if (
        len(silences["silences"]) < max_process
    ):  # 少于 max_process 个 silence 就不用分配任务了
        _tasks = []
        for i in range(silences_count):
            _tasks.append([i])
        return _tasks

    total_duration = total_silence_duration(silences)

    avg_cost = (
        total_duration + FFMPEG_COST * (silences_count + REDUNDANCE_COST_RATE)
    ) / max_process
    process_costs: list[int | Decimal] = [0] * max_process
    process_tasks: list[list[int]] = [
        [] for _ in range(max_process)
    ]  # 这里不能直接乘, 不然列表都是同一个引用

    def _get_next_cost(process_index: int, duration: Decimal) -> int | Decimal:
        return (
            process_costs[process_index] + duration * DURATION_COST_RATE + FFMPEG_COST
        )

    for task_index, (_start, _end, duration) in enumerate(silences["silences"]):
        min_cost_index = process_costs.index(min(process_costs))
        _next_cost = _get_next_cost(min_cost_index, duration)

        if _next_cost <= avg_cost:
            process_tasks[min_cost_index].append(task_index)
            process_costs[min_cost_index] = _next_cost
        else:
            for i in range(max_process):
                if i == min_cost_index:
                    continue

                _next_cost = _get_next_cost(i, duration)
                if _next_cost <= avg_cost:
                    process_tasks[i].append(task_index)
                    process_costs[i] = _next_cost
                    break
            else:  # 只能加到最小的那个进程了
                _next_cost = _get_next_cost(min_cost_index, duration)
                process_tasks[min_cost_index].append(task_index)
                process_costs[min_cost_index] = _next_cost
                # raise RuntimeError(
                #     f"cannot assign task to any process for task_index `{task_index}` (from {_start} to {_end})"
                # )
    return process_tasks  # type: ignore


async def main(args) -> int | None:
    SLEEP_BEFORE_PROCESS: int | float = 3

    await check_ffmpeg()

    input_file: Path = args.input.absolute().resolve()
    output: Path = args.output.absolute().resolve()
    merge_parts: bool = args.merge_parts
    audio_index: int = args.audio_index
    silence_db: float = args.silence_db
    silence_duration: float = args.silence_duration
    processes: int = args.processes
    non_print_progress: bool = args.non_print_progress
    output_suffix: str = args.output_suffix
    _use_auto_suffix: bool = (
        output_suffix == "auto"
    )  # 避免用户使用 `.auto` 后缀 (虽然 FFmpeg 会骂他就是了)
    if output_suffix.startswith("."):
        output_suffix = output_suffix[1:]

    assert (
        merge_parts or output.is_dir()
    ), "output cannot be a directory if merge_parts is true"
    assert (
        not merge_parts or output.parent.is_dir()
    ), "the directory of output file does not exist"

    output_dir = output.parent / ".silenced_parts" if merge_parts else output
    output_dir.mkdir(parents=True, exist_ok=True)

    print("INFO: getting media info...")
    media_info = await get_media_info(input_file)
    audio_streams = list(get_audios(media_info))
    audios_count = len(audio_streams)
    if audio_index is None:
        if audios_count > 1:
            while True:
                try:
                    audio_index = int(
                        input(
                            f"WARNING: more than one audio stream found, please specify audio_index (0 - {audios_count - 1}): "
                        )
                    )

                    assert (
                        audio_index < audios_count
                    ), f"audio_index {audio_index} out of range (must <= {audios_count - 1})"
                    break
                except ValueError:
                    print("ERROR: audio_index must be an integer")
                except AssertionError:
                    print(
                        f"ERROR: audio_index out of range (must <= {audios_count - 1})"
                    )
        else:
            audio_index = 0

    assert (
        audio_index < audios_count
    ), f"audio_index {audio_index} out of range (must <= {audios_count - 1})"
    print(f"INFO: audio streams got, use audio_index: {audio_index} to get silences")

    suffix = get_suffix(audio_streams[audio_index]["codec_name"])
    silences = await get_silence(
        input_file,
        silence_db=silence_db,
        silence_duration=silence_duration,
        audio_index=audio_index,
        suffix=suffix if _use_auto_suffix else output_suffix,
    )
    silences_count = len(silences["silences"])
    if silences_count <= 1:
        print("INFO: no silences found, exit")
        return

    print(
        f"SUCCESS: got silences successfully! {silences_count} silences found, total duration: {total_silence_duration(silences)} seconds. Assigning tasks..."
    )

    process_tasks = assign_tasks(silences, processes)

    print(
        f"SUCCESS: assigned tasks successfully! will start to split silences which use {processes} processes in 3 seconds."
    )

    print()
    _range_target = SLEEP_BEFORE_PROCESS * 10 // 1
    for i in range(1, _range_target + 1):
        if not i % 5:
            print(f"{(_range_target - i) / 10}s left...", end="\r")
        await asyncio.sleep(0.1)
    print()

    tasks = [
        split_silences(
            silences, task_range, output_dir, print_progress=not non_print_progress
        )
        for task_range in process_tasks
        if task_range
    ]
    results = await asyncio.gather(*tasks)
    print()

    filelist_file = output_dir / ".filelist"
    with filelist_file.open("w+", encoding="u8") as fp:
        errored = False
        for result in results:
            for status, file in result:
                status: int
                file: Path

                if not status:
                    fp.write(f"file '{file.absolute().resolve()}'\n")
                    continue

                errored = True
                print(
                    f"WARNING: ffmpeg process exited with status {status} while processing {file.name}"
                )

    if not errored:
        print("SUCCESS: tasks finished with no error.")

    if not merge_parts:
        return

    print("INFO: start to merge parts...")

    status = await merge_parts_func(
        output.with_suffix(f".{suffix}") if _use_auto_suffix else output,
        filelist_file,
        print_progress=not non_print_progress,
    )

    if not status:
        for file in output_dir.iterdir():
            file.unlink(True)
        output_dir.rmdir()

        print("SUCCESS: merged parts successfully!")
        return

    print(f"ERROR: ffmpeg process exited with status {status}.")


if __name__ == "__main__":
    processes_default = os.cpu_count()
    processes_default = processes_default // 2 if processes_default else 4
    if processes_default < 1:
        processes_default = 1

    from argparse import ArgumentParser

    parser = ArgumentParser(
        prog="ffmpeg-audio-splitter",
        description="split audio by silence",
        epilog="by lovemilk",
    )

    parser.add_argument("input", type=Path, help="input file")
    parser.add_argument("-o", "--output", required=True, type=Path, help="output file")
    parser.add_argument(
        "-os",
        "--output-suffix",
        type=str,
        default="auto",
        help='output file suffix ("auto" to use "codec_name")',
    )
    parser.add_argument(
        "-s", "--silence-db", required=True, type=float, help="silence threshold in dB"
    )
    parser.add_argument(
        "-sd",
        "--silence-duration",
        required=True,
        type=float,
        help="silence time in seconds",
    )
    parser.add_argument(
        "-a",
        "--audio-index",
        type=int,
        default=None,
        help="audio stream index (from 0 to start)",
    )
    parser.add_argument(
        "-p",
        "--processes",
        type=int,
        default=processes_default,
        help="number of processes",
    )
    parser.add_argument(
        "-m", "--merge-parts", action="store_true", help="merge parts into one file"
    )
    parser.add_argument(
        "-np",
        "--non-print-progress",
        action="store_true",
        help="not print ffmpeg progress or yes",
    )

    args = parser.parse_args()

    sys.exit(asyncio.run(main(args)))
