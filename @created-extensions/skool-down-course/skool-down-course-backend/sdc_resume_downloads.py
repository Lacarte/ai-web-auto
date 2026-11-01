"""
SDC Resume / Verify Downloads
-----------------------------
Walks a skool-output course tree and for every lesson:

  1. Probes the final <safe_name>.mp4 with ffprobe.
  2. Compares actual duration vs expected (from meta.json).
  3. Classifies as:
       - ok         : complete, no leftover partial files
       - cleanup    : complete, BUT leftover .part/.ytdl/fhls-raw-*/video_url.txt exists
       - incomplete : final .mp4 too short vs expected duration
       - corrupt    : final .mp4 exists but ffprobe cannot read it
       - missing    : no final .mp4
       - skool-native-incomplete : cannot resume via yt-dlp (re-run sdc_downloader.py)
  4. Acts:
       - 'cleanup'                -> delete leftover partial files
       - 'incomplete' / 'corrupt' -> delete broken final + resume via yt-dlp
       - 'missing'                -> resume via yt-dlp
       - 'ok'                     -> no action
       - skool-native-incomplete  -> reported, not touched

Usage:
  python sdc_resume_downloads.py <course_dir> [--dry-run] [--tolerance 0.95]

Examples:
  python sdc_resume_downloads.py "../../../skool-output/Classroom_2026-04-21" --dry-run
  python sdc_resume_downloads.py "../../../skool-output/Classroom_2026-04-21"
"""
import argparse
import asyncio
import glob as _glob
import json
import os
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>")


def _find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    backend_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    for d in [backend_dir / 'venv', backend_dir.parent.parent.parent / 'venv']:
        p = d / 'Scripts' / f'{name}.exe'
        if p.exists():
            return str(p)
    return name


YT_DLP = _find_tool('yt-dlp')
FFPROBE = _find_tool('ffprobe')

RESUMABLE_TYPES = {'Loom', 'YouTube', 'Vimeo', 'Wistia', 'Video'}
LEFTOVER_GLOBS = (
    '{n}.*.part',
    '{n}.*.ytdl',
    '{n}.*.part-Frag*',
    '{n}.fhls-raw-*',
    '{n}_video_url.txt',   # written by sdc_backend.py
    '{n}.video_url.txt',   # written by sdc_downloader.py
)

# Both writers name the URL-info sidecar differently; readers must accept both.
URL_TXT_SUFFIXES = ('_video_url.txt', '.video_url.txt')


def meta_expected_duration_ms(meta):
    """Extract expected duration (ms) from whichever meta schema was used."""
    for k in ('videoDurationMs', 'videoLenMs'):
        v = meta.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    info = meta.get('videoInfo') or {}
    for k in ('videoDurationMs', 'videoLenMs', 'durationMs'):
        v = info.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def meta_url_and_type(meta):
    url = meta.get('videoUrl') or meta.get('videoLink') or ''
    vtype = meta.get('videoType') or ''
    if not vtype:
        info = meta.get('videoInfo') or {}
        vtype = info.get('type') or ''
        if not url:
            url = info.get('url') or info.get('embedUrl') or ''
    if not vtype and url:
        u = url.lower()
        if 'loom.com' in u:
            vtype = 'Loom'
        elif 'youtube.com' in u or 'youtu.be' in u:
            vtype = 'YouTube'
        elif 'vimeo.com' in u:
            vtype = 'Vimeo'
        elif 'wistia' in u:
            vtype = 'Wistia'
    if not vtype and (meta.get('videoId') or (meta.get('videoInfo') or {}).get('playbackId')):
        vtype = 'Skool'
    return url, vtype


def find_leftovers(lesson_dir, safe_name):
    seen = set()
    found = []
    # Lesson titles keep '[' and ']' after sanitize; unescaped they would be
    # read as a glob character class and silently match nothing.
    stem = _glob.escape(safe_name)
    for pat in LEFTOVER_GLOBS:
        for p in lesson_dir.glob(pat.format(n=stem)):
            if p in seen:
                continue
            seen.add(p)
            found.append(p)
    return found


def ffprobe_duration_seconds(path):
    """Return float seconds, or None if ffprobe fails."""
    try:
        import subprocess
        res = subprocess.run(
            [FFPROBE, '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1',
             str(path)],
            capture_output=True, text=True, timeout=30
        )
        if res.returncode != 0:
            return None
        out = res.stdout.strip()
        if not out or out.lower() == 'n/a':
            return None
        return float(out)
    except Exception as e:
        logger.debug(f"ffprobe error on {path.name}: {e}")
        return None


def classify(lesson_dir, safe_name, meta, tolerance):
    """Return (status, detail, expected_ms, actual_ms, leftovers, url, vtype)."""
    final_path = lesson_dir / f'{safe_name}.mp4'
    leftovers = find_leftovers(lesson_dir, safe_name)
    expected_ms = meta_expected_duration_ms(meta)
    url, vtype = meta_url_and_type(meta)

    if not final_path.exists():
        # Lesson never had a video at all (no url, no videoId, no leftovers) -> not actionable
        has_video_ref = bool(url) or bool(meta.get('videoId') or (meta.get('videoInfo') or {}).get('playbackId'))
        if not has_video_ref and not leftovers:
            return 'no-video', 'lesson has no video reference', expected_ms, None, leftovers, url, vtype
        if vtype == 'Skool':
            return 'skool-native-incomplete', 'no final .mp4 (Skool native)', expected_ms, None, leftovers, url, vtype
        return 'missing', 'no final .mp4', expected_ms, None, leftovers, url, vtype

    actual_s = ffprobe_duration_seconds(final_path)
    if actual_s is None:
        return 'corrupt', 'ffprobe cannot read file', expected_ms, None, leftovers, url, vtype
    actual_ms = actual_s * 1000.0

    if expected_ms and actual_ms < expected_ms * tolerance:
        pct = (actual_ms / expected_ms) * 100
        detail = f'{actual_s:.0f}s / {expected_ms/1000:.0f}s expected ({pct:.1f}%)'
        if vtype == 'Skool':
            return 'skool-native-incomplete', detail, expected_ms, actual_ms, leftovers, url, vtype
        return 'incomplete', detail, expected_ms, actual_ms, leftovers, url, vtype

    if leftovers:
        return 'cleanup', f'complete ({actual_s:.0f}s), {len(leftovers)} leftover(s)', expected_ms, actual_ms, leftovers, url, vtype
    return 'ok', f'complete ({actual_s:.0f}s)', expected_ms, actual_ms, leftovers, url, vtype


def _read_video_url_txt(path):
    """Return {'url':..., 'type':...} from a *_video_url.txt file, or {}."""
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return {}
    out = {}
    for line in lines:
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        k = k.strip().lower()
        v = v.strip()
        if not v or v.upper() == 'N/A':
            continue
        if k in ('url', 'video url'):
            out['videoUrl'] = v
        elif k == 'type':
            out['videoType'] = v
        elif k in ('embed', 'embed url') and 'videoUrl' not in out:
            out['videoUrl'] = v
        elif k in ('stream', 'stream url') and 'videoUrl' not in out:
            out['videoUrl'] = v
    return out


def _stems_in_flat_folder(folder):
    """Find every unique video-stem in a flat folder (no meta.json per file)."""
    stems = set()
    for p in folder.iterdir():
        if not p.is_file():
            continue
        n = p.name
        stem = None
        sidecar = next((s for s in URL_TXT_SUFFIXES if n.endswith(s)), None)
        if sidecar:
            stem = n[:-len(sidecar)]
        elif '.fhls-raw-' in n:
            stem = n.split('.fhls-raw-')[0]
        elif n.endswith('.mp4'):
            stem = n[:-len('.mp4')]
        elif n.endswith('.mp4.part') or '.mp4.part' in n or n.endswith('.ytdl'):
            base = n
            for suf in ('.ytdl',):
                if base.endswith(suf):
                    base = base[:-len(suf)]
            base = base.split('.mp4')[0]
            stem = base
        if stem:
            stems.add(stem)
    return stems


def scan_flat_folder(folder, tolerance):
    """Treat each unique stem as a 'lesson' (no meta.json, no expected duration)."""
    results = []
    if not folder.exists() or not folder.is_dir():
        return results
    for stem in _stems_in_flat_folder(folder):
        meta = {}
        for suffix in URL_TXT_SUFFIXES:
            url_txt = folder / f'{stem}{suffix}'
            if url_txt.exists():
                meta.update(_read_video_url_txt(url_txt))
                break
        c = classify(folder, stem, meta, tolerance)
        results.append((folder, stem, meta, *c))
    return results


def scan_tree(course_dir, tolerance):
    results = []
    for meta_path in course_dir.rglob('*_meta.json'):
        lesson_dir = meta_path.parent
        safe_name = meta_path.name[:-len('_meta.json')]
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f"Bad meta.json {meta_path}: {e}")
            continue
        c = classify(lesson_dir, safe_name, meta, tolerance)
        results.append((lesson_dir, safe_name, meta, *c))

    # Also scan community/ subfolder (flat layout, no meta.json per video).
    for community_dir in course_dir.glob('community'):
        logger.info(f"Scanning flat folder: {community_dir.relative_to(course_dir)}")
        results.extend(scan_flat_folder(community_dir, tolerance))
    return results


def clean_leftovers(files, dry_run=False):
    removed = 0
    for f in files:
        if dry_run:
            logger.info(f"    (dry-run) would delete: {f.name}")
            removed += 1
            continue
        try:
            f.unlink()
            logger.info(f"    deleted: {f.name}")
            removed += 1
        except Exception as e:
            logger.warning(f"    could not delete {f.name}: {e}")
    return removed


def clean_broken_final(final_path, dry_run=False):
    if not final_path.exists():
        return
    if dry_run:
        logger.info(f"    (dry-run) would delete broken final: {final_path.name}")
        return
    try:
        final_path.unlink()
        logger.info(f"    deleted broken final: {final_path.name}")
    except Exception as e:
        logger.warning(f"    could not delete {final_path.name}: {e}")


def clean_loom_url(url):
    if not url:
        return url
    if '/embed/' in url:
        return url.split('?')[0].replace('/embed/', '/share/')
    return url


async def resume_one(lesson_dir, safe_name, url):
    output_path = lesson_dir / f'{safe_name}.mp4'
    dl_url = clean_loom_url(url)
    logger.info(f"    yt-dlp: {dl_url[:80]}")
    try:
        proc = await asyncio.create_subprocess_exec(
            YT_DLP,
            '--no-warnings',
            '--continue',
            '--retries', '10',
            '--fragment-retries', '20',
            '--retry-sleep', 'fragment:exp=1:10',
            '--socket-timeout', '30',
            '--concurrent-fragments', '4',
            '-o', str(output_path),
            '--merge-output-format', 'mp4',
            dl_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)
        if proc.returncode == 0 and output_path.exists():
            mb = output_path.stat().st_size / (1024 * 1024)
            logger.success(f"    OK {mb:.1f} MB -> {output_path.name}")
            return True
        logger.error(f"    FAIL rc={proc.returncode}: {stderr.decode(errors='replace')[-400:]}")
        return False
    except FileNotFoundError:
        logger.error(f"yt-dlp not found at {YT_DLP}")
        return False
    except asyncio.TimeoutError:
        logger.error("    TIMEOUT after 3600s (state saved; re-run to continue)")
        try:
            proc.kill()
            await proc.communicate()
        except Exception:
            pass
        return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('course_dir', help='Path to skool-output/<course>_<date> folder')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report only - no deletes, no downloads')
    ap.add_argument('--tolerance', type=float, default=0.95,
                    help='Min actual/expected duration ratio to treat as complete (default 0.95)')
    ap.add_argument('--keep-parts', action='store_true',
                    help='Do not delete leftover .part/.ytdl/fhls-raw files even if complete')
    args = ap.parse_args()

    course_dir = Path(args.course_dir).resolve()
    if not course_dir.exists():
        logger.error(f"Course dir does not exist: {course_dir}")
        sys.exit(2)

    logger.info(f"Scanning: {course_dir}")
    logger.info(f"ffprobe: {FFPROBE}")
    logger.info(f"yt-dlp:  {YT_DLP}")
    logger.info(f"tolerance: {args.tolerance:.2f}  dry-run: {args.dry_run}\n")

    results = scan_tree(course_dir, args.tolerance)

    counts = {}
    for r in results:
        counts[r[3]] = counts.get(r[3], 0) + 1

    logger.info("Classification summary:")
    for k, v in sorted(counts.items()):
        logger.info(f"  {k:<26}: {v}")
    logger.info("")

    # First pass: report actionable items only (skip 'ok' and 'no-video')
    for lesson_dir, safe_name, meta, status, detail, exp_ms, act_ms, leftovers, url, vtype in results:
        if status in ('ok', 'no-video'):
            continue
        rel = lesson_dir.relative_to(course_dir)
        logger.info(f"[{status}] [{vtype or '?'}] {rel}")
        logger.info(f"    {detail}")
        if leftovers:
            logger.info(f"    leftovers: {', '.join(p.name for p in leftovers)}")

    # Second pass: act
    if args.dry_run:
        logger.info("\n(dry-run) no changes made.")
        return

    cleaned = 0
    resumed_ok = 0
    resumed_fail = 0
    blocked_skool = 0

    logger.info("\n" + "=" * 50)
    logger.info("Applying actions")
    logger.info("=" * 50)

    for lesson_dir, safe_name, meta, status, detail, exp_ms, act_ms, leftovers, url, vtype in results:
        rel = lesson_dir.relative_to(course_dir)

        if status == 'ok':
            continue

        if status == 'cleanup':
            if args.keep_parts:
                continue
            logger.info(f"\n[cleanup] {rel}")
            cleaned += clean_leftovers(leftovers, dry_run=False)
            continue

        if status == 'skool-native-incomplete':
            logger.warning(f"\n[skool-native] {rel}  -- re-run sdc_downloader.py for this course; skipping")
            blocked_skool += 1
            continue

        if status in ('missing', 'incomplete', 'corrupt'):
            if not url or vtype not in RESUMABLE_TYPES:
                logger.warning(f"\n[skip] {rel}  -- no resumable URL (vtype={vtype!r})")
                continue
            logger.info(f"\n[resume] {rel}  ({status})")
            if status in ('incomplete', 'corrupt'):
                clean_broken_final(lesson_dir / f'{safe_name}.mp4', dry_run=False)
            ok = await resume_one(lesson_dir, safe_name, url)
            if ok:
                resumed_ok += 1
                # Re-verify after resume, then clean leftovers
                new_dur = ffprobe_duration_seconds(lesson_dir / f'{safe_name}.mp4')
                if new_dur is not None and (not exp_ms or new_dur * 1000 >= exp_ms * args.tolerance):
                    new_left = find_leftovers(lesson_dir, safe_name)
                    if new_left and not args.keep_parts:
                        clean_leftovers(new_left, dry_run=False)
            else:
                resumed_fail += 1

    logger.info("\n" + "=" * 50)
    logger.info(f"Done.  cleaned={cleaned}  resumed_ok={resumed_ok}  "
                f"resumed_fail={resumed_fail}  skool_blocked={blocked_skool}")
    logger.info("=" * 50)


if __name__ == '__main__':
    asyncio.run(main())
