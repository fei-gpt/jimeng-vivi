#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List


TAIL_REFERENCE_TIMESTAMPS = [14.8, 14.6, 14.5, 14.3, 14.0]


def run_media_command(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )


def require_success(proc: subprocess.CompletedProcess, action: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{action} failed with exit {proc.returncode}: {proc.stdout}")


def extract_frame(video: Path, timestamp: float, output_image: Path) -> Path:
    video = Path(video)
    output_image = Path(output_image)
    output_image.parent.mkdir(parents=True, exist_ok=True)
    proc = run_media_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{float(timestamp):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_image),
        ]
    )
    require_success(proc, f"extract frame at {timestamp}s")
    if not output_image.exists() or output_image.stat().st_size <= 0:
        raise RuntimeError(f"extract frame produced no image: {output_image}")
    return output_image


def is_black_image(image: Path, threshold: int = 8, ratio: float = 0.95) -> bool:
    from PIL import Image

    image = Path(image)
    with Image.open(image) as img:
        gray = img.convert("L")
        pixels = list(gray.getdata())
    if not pixels:
        return True
    near_black = sum(1 for value in pixels if value <= threshold)
    average = sum(pixels) / len(pixels)
    return average <= threshold and (near_black / len(pixels)) >= ratio


def extract_tail_reference_frame(
    video: Path,
    output_image: Path,
    threshold: int = 8,
    ratio: float = 0.95,
    timestamps: List[float] | None = None,
) -> Dict[str, object]:
    attempts = timestamps or TAIL_REFERENCE_TIMESTAMPS
    failures: List[dict] = []
    for timestamp in attempts:
        candidate = output_image.with_name(f"{output_image.stem}-{str(timestamp).replace('.', '_')}s{output_image.suffix}")
        try:
            extract_frame(video, timestamp, candidate)
            black = is_black_image(candidate, threshold=threshold, ratio=ratio)
            if not black:
                if candidate != output_image:
                    output_image.parent.mkdir(parents=True, exist_ok=True)
                    output_image.write_bytes(candidate.read_bytes())
                return {
                    "path": str(output_image),
                    "timestamp": timestamp,
                    "attempts": failures + [{"timestamp": timestamp, "black": False, "path": str(candidate)}],
                }
            failures.append({"timestamp": timestamp, "black": True, "path": str(candidate)})
        except Exception as exc:
            failures.append({"timestamp": timestamp, "error": str(exc), "path": str(candidate)})
    raise RuntimeError(f"No usable tail reference frame found: {json.dumps(failures, ensure_ascii=False)}")


def clip_video(video: Path, start: float, duration: float, output_video: Path) -> Path:
    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)
    proc = run_media_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{float(start):.3f}",
            "-i",
            str(video),
            "-t",
            f"{float(duration):.3f}",
            "-c",
            "copy",
            str(output_video),
        ]
    )
    require_success(proc, f"clip video from {start}s for {duration}s")
    return output_video


def video_probe(video: Path) -> dict:
    proc = run_media_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(video),
        ]
    )
    require_success(proc, "probe video")
    return json.loads(proc.stdout or "{}")


def evaluate_concat_compatibility(video_paths: List[Path]) -> dict:
    probes = [video_probe(path) for path in video_paths]

    def video_signature(probe: dict) -> tuple:
        stream = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
        return (
            stream.get("codec_name"),
            stream.get("width"),
            stream.get("height"),
            stream.get("r_frame_rate"),
            stream.get("pix_fmt"),
        )

    signatures = [video_signature(probe) for probe in probes]
    return {
        "can_concat_without_reencode": len(set(signatures)) <= 1,
        "signatures": signatures,
    }


def concat_videos_copy(video_paths: List[Path], output_video: Path) -> Path:
    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        list_file = Path(handle.name)
        for path in video_paths:
            escaped = str(Path(path)).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    try:
        proc = run_media_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(output_video),
            ]
        )
        require_success(proc, "concat videos without reencode")
    finally:
        try:
            list_file.unlink()
        except FileNotFoundError:
            pass
    if not output_video.exists() or output_video.stat().st_size <= 0:
        raise RuntimeError(f"concat produced no video: {output_video}")
    return output_video


def concat_videos_reencode(video_paths: List[Path], output_video: Path) -> Path:
    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)
    if output_video.exists():
        output_video.unlink()
    inputs: List[str] = []
    for path in video_paths:
        inputs.extend(["-i", str(Path(path))])
    stream_count = len(video_paths)
    probes = [video_probe(path) for path in video_paths]
    first_video = next((item for item in probes[0].get("streams", []) if item.get("codec_type") == "video"), {})
    width = int(first_video.get("width") or 0) or 720
    height = int(first_video.get("height") or 0) or 1280
    video_filters = []
    for index in range(stream_count):
        video_filters.append(
            f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]"
        )
    all_have_audio = all(any(item.get("codec_type") == "audio" for item in probe.get("streams", [])) for probe in probes)
    if all_have_audio:
        concat_inputs = "".join(f"[v{index}][{index}:a:0]" for index in range(stream_count))
        filter_complex = ";".join(video_filters + [f"{concat_inputs}concat=n={stream_count}:v=1:a=1[v][a]"])
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac"]
    else:
        concat_inputs = "".join(f"[v{index}]" for index in range(stream_count))
        filter_complex = ";".join(video_filters + [f"{concat_inputs}concat=n={stream_count}:v=1:a=0[v]"])
        maps = ["-map", "[v]"]
    proc = run_media_command(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            *maps,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
    )
    if proc.returncode == 0 and output_video.exists() and output_video.stat().st_size > 0:
        return output_video

    if output_video.exists():
        output_video.unlink()
    concat_inputs = "".join(f"[v{index}]" for index in range(stream_count))
    filter_complex = ";".join(video_filters + [f"{concat_inputs}concat=n={stream_count}:v=1:a=0[v]"])
    proc = run_media_command(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
    )
    require_success(proc, "concat videos with reencode")
    if not output_video.exists() or output_video.stat().st_size <= 0:
        raise RuntimeError(f"reencode concat produced no video: {output_video}")
    return output_video


def stitch_videos(video_paths: List[Path], output_video: Path) -> dict:
    paths = [Path(path) for path in video_paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        return {"status": "failed", "mode": "", "error": f"missing video files: {missing}"}
    try:
        compatibility = evaluate_concat_compatibility(paths)
        if compatibility.get("can_concat_without_reencode"):
            concat_videos_copy(paths, output_video)
            mode = "copy"
        else:
            concat_videos_reencode(paths, output_video)
            mode = "reencode"
        return {
            "status": "success",
            "mode": mode,
            "error": "",
            "output": str(output_video),
            "compatibility": compatibility,
        }
    except Exception as exc:
        try:
            concat_videos_reencode(paths, output_video)
            return {
                "status": "success",
                "mode": "reencode",
                "error": "",
                "output": str(output_video),
                "compatibility": compatibility if "compatibility" in locals() else {},
            }
        except Exception as retry_exc:
            return {
                "status": "failed",
                "mode": "reencode",
                "error": f"{exc}; reencode retry failed: {retry_exc}",
                "compatibility": compatibility if "compatibility" in locals() else {},
            }
