"""Download, verify, and extract the public Sentinel-1 oil-spill dataset.

Selected records:
- Part I  : 10.5281/zenodo.8346860 (oil images + oil masks)
- Part II : 10.5281/zenodo.8253899 (no-oil + look-alike images)
- Part III: 10.5281/zenodo.13761290 (independent test images + masks)

The Part II mask archives are intentionally not downloaded. The source dataset
specifies oil-free and look-alike masks as zero for the oil-segmentation target,
so Varuna-Netra generates those negative masks in memory.

Downloads are resumable. Every completed archive is checked against the MD5
published by Zenodo before extraction. By default, a verified archive is deleted
after successful extraction to reduce peak disk usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote

import requests
from tqdm import tqdm


DATASETS = {
    "part1": {
        "record_id": "8346860",
        "doi": "10.5281/zenodo.8346860",
        "files": [
            {
                "name": "01_Train_Val_Oil_Spill_images.7z",
                "md5": "e2a6a5b473ca587474d8daee9cd54e10",
            },
            {
                "name": "01_Train_Val_Oil_Spill_mask.7z",
                "md5": "9bc53c38db2ab82d15bf6914352403ef",
            },
        ],
    },
    "part2": {
        "record_id": "8253899",
        "doi": "10.5281/zenodo.8253899",
        "files": [
            {
                "name": "01_Train_Val_Lookalike_images.7z",
                "md5": "e0af26e0b2ad7979b889a91ed5df9a41",
            },
            {
                "name": "01_Train_Val_No_Oil_Images.7z",
                "md5": "6836df2bea59ebb73479ffea71828e14",
            },
        ],
    },
    "part3": {
        "record_id": "13761290",
        "doi": "10.5281/zenodo.13761290",
        "files": [
            {
                "name": "02_Test_images_and_ground_truth.7z",
                "md5": "5dce64cd7ff9d80189d13504bd3bcbf5",
            }
        ],
    },
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", default="data/zenodo_sar", help="Dataset destination")
    p.add_argument(
        "--only",
        choices=("all", "train", "test", "part1", "part2", "part3"),
        default="all",
        help="Download all data, training data only, test data only, or one record",
    )
    p.add_argument("--keep-archives", action="store_true", help="Keep .7z files after extraction")
    p.add_argument("--skip-verify", action="store_true", help="Skip MD5 verification (not recommended)")
    p.add_argument("--chunk-mib", type=int, default=8, help="Streaming download chunk size")
    p.add_argument("--timeout", type=int, default=90, help="HTTP read timeout in seconds")
    return p.parse_args()


def selected_parts(value: str) -> list[str]:
    if value == "all":
        return ["part1", "part2", "part3"]
    if value == "train":
        return ["part1", "part2"]
    if value == "test":
        return ["part3"]
    return [value]


def zenodo_url(record_id: str, filename: str) -> str:
    return f"https://zenodo.org/records/{record_id}/files/{quote(filename)}?download=1"


def file_md5(path: Path, block_mib: int = 16) -> str:
    digest = hashlib.md5()  # nosec B324 - used only for published file integrity verification
    with path.open("rb") as fh:
        while True:
            block = fh.read(block_mib * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_resumable(url: str, destination: Path, chunk_mib: int, timeout: int):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}

    response = requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=(20, timeout),
        allow_redirects=True,
    )
    if response.status_code == 416 and existing:
        # A stale .part can be larger than the remote object. Start over safely.
        partial.unlink(missing_ok=True)
        return download_resumable(url, destination, chunk_mib, timeout)
    response.raise_for_status()

    if existing and response.status_code != 206:
        # Server ignored Range; never append a second full archive to the partial file.
        existing = 0
        partial.unlink(missing_ok=True)

    content_length = int(response.headers.get("Content-Length") or 0)
    total = existing + content_length if content_length else None
    mode = "ab" if existing else "wb"
    chunk_size = max(1, chunk_mib) * 1024 * 1024

    with partial.open(mode) as fh, tqdm(
        total=total,
        initial=existing,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=destination.name,
    ) as progress:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                fh.write(chunk)
                progress.update(len(chunk))

    partial.replace(destination)


def _extract_with_system_7z(archive: Path, destination: Path) -> bool:
    executable = next((shutil.which(name) for name in ("7zz", "7z", "7za") if shutil.which(name)), None)
    if not executable:
        return False
    subprocess.run(
        [executable, "x", str(archive), f"-o{destination}", "-y"],
        check=True,
    )
    return True


def extract_archive(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    if _extract_with_system_7z(archive, destination):
        return
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError(
            "No 7z executable or py7zr found. Install backend/training/requirements-ml.txt."
        ) from exc
    with py7zr.SevenZipFile(archive, mode="r") as zf:
        zf.extractall(path=destination)


def prepare_one(part: str, root: Path, keep_archives: bool, verify: bool, chunk_mib: int, timeout: int):
    spec = DATASETS[part]
    part_root = root / part
    archive_root = root / "archives"
    marker_root = root / ".prepared"
    marker_root.mkdir(parents=True, exist_ok=True)

    for item in spec["files"]:
        name = item["name"]
        expected_md5 = item["md5"]
        marker = marker_root / f"{part}--{name}.json"
        if marker.exists():
            print(f"[skip] already prepared: {name}")
            continue

        archive = archive_root / name
        if not archive.exists():
            print(f"[download] {spec['doi']} :: {name}")
            download_resumable(
                zenodo_url(spec["record_id"], name), archive, chunk_mib, timeout
            )
        else:
            print(f"[reuse] archive already present: {archive}")

        actual_md5 = None
        if verify:
            print(f"[verify] MD5 {name}")
            actual_md5 = file_md5(archive)
            if actual_md5.lower() != expected_md5.lower():
                bad = archive.with_suffix(archive.suffix + ".bad")
                archive.replace(bad)
                raise RuntimeError(
                    f"Checksum mismatch for {name}: expected {expected_md5}, got {actual_md5}. "
                    f"The bad file was moved to {bad}."
                )

        print(f"[extract] {name} -> {part_root}")
        extract_archive(archive, part_root)
        marker.write_text(
            json.dumps(
                {
                    "record_id": spec["record_id"],
                    "doi": spec["doi"],
                    "file": name,
                    "expected_md5": expected_md5,
                    "verified_md5": actual_md5,
                    "extracted_to": str(part_root),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not keep_archives:
            archive.unlink(missing_ok=True)
            print(f"[cleanup] removed verified archive: {name}")


def count_tiffs(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".tif", ".tiff"})


def write_manifest(root: Path):
    summary = {
        "source": "Zenodo Sentinel-1 SAR Oil Spill Dataset Parts I-III",
        "records": {k: {"record_id": v["record_id"], "doi": v["doi"]} for k, v in DATASETS.items()},
        "tiff_counts_after_extraction": {part: count_tiffs(root / part) for part in DATASETS},
        "part2_negative_masks": "generated as zeros in memory; published zero-mask archives are not required",
    }
    (root / "dataset_manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    args = parse_args()
    root = Path(args.data_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    free_gib = shutil.disk_usage(root).free / (1024 ** 3)
    print(f"Data root: {root}")
    print(f"Free disk: {free_gib:.1f} GiB")
    if args.only in {"all", "train"} and free_gib < 120:
        print(
            "WARNING: the training imagery is very large. For the full pipeline, "
            "160-200+ GiB free is a safer target when archives are deleted after extraction.",
            file=sys.stderr,
        )

    for part in selected_parts(args.only):
        prepare_one(
            part,
            root,
            keep_archives=args.keep_archives,
            verify=not args.skip_verify,
            chunk_mib=args.chunk_mib,
            timeout=args.timeout,
        )
    write_manifest(root)
    print("Dataset preparation complete.")


if __name__ == "__main__":
    main()
