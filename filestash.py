#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DATE_PATTERN = re.compile(r"^(?P<date>\d{4}\.\d{2}\.\d{2})\s*-\s*(?P<rest>.+)$")
SAFE_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass
class Config:
    source_dir: Path
    destination_root: Path
    review_dir: Path
    file_extensions: List[str]
    company_aliases: Dict[str, str]
    medical_people: Dict[str, str]
    medical_companies: List[str]
    move_files: bool


@dataclass
class PlannedAction:
    source: Path
    destination: Optional[Path]
    status: str
    reason: str


def normalize_key(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value.strip().lower())


def sanitize_filename_component(value: str) -> str:
    value = SAFE_CHARS_PATTERN.sub("", value)
    value = WHITESPACE_PATTERN.sub(" ", value).strip()
    return value


def parse_config(path: Path) -> Config:
    data = json.loads(path.read_text(encoding="utf-8"))

    source_dir = Path(data["source_dir"]).expanduser()
    destination_root = Path(data["destination_root"]).expanduser()
    review_dir = Path(data.get("review_dir", str(destination_root / "_Needs Review"))).expanduser()

    extensions = [ext.lower() for ext in data.get("file_extensions", [".pdf"])]
    aliases_raw = data.get("company_aliases", {})
    company_aliases = {normalize_key(k): v.strip() for k, v in aliases_raw.items() if v.strip()}

    people_raw = data.get("medical_people", {})
    medical_people = {normalize_key(k): v.strip() for k, v in people_raw.items() if v.strip()}
    medical_companies = [normalize_key(c) for c in data.get("medical_companies", [])]

    move_files = bool(data.get("move_files", True))

    return Config(
        source_dir=source_dir,
        destination_root=destination_root,
        review_dir=review_dir,
        file_extensions=extensions,
        company_aliases=company_aliases,
        medical_people=medical_people,
        medical_companies=medical_companies,
        move_files=move_files,
    )


def load_index(index_path: Path) -> Dict[str, List[str]]:
    if not index_path.exists():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            clean = {}
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, list):
                    clean[k] = [str(item) for item in v]
            return clean
    except json.JSONDecodeError:
        pass
    return {}


def save_index(index_path: Path, index_data: Dict[str, List[str]]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_data, indent=2, sort_keys=True), encoding="utf-8")


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def split_filename_parts(name_without_ext: str) -> Tuple[Optional[str], List[str]]:
    match = DATE_PATTERN.match(name_without_ext)
    if not match:
        return None, []

    date_str = match.group("date")
    rest = match.group("rest")
    parts = [part.strip() for part in rest.split(" - ") if part.strip()]
    if not parts:
        return None, []
    return date_str, parts


def canonical_company(company_raw: str, aliases: Dict[str, str]) -> str:
    return aliases.get(normalize_key(company_raw), company_raw.strip())


def extract_person(parts: List[str], medical_people: Dict[str, str]) -> Optional[str]:
    for part in parts:
        person = medical_people.get(normalize_key(part))
        if person:
            return person
    return None


def is_medical(company: str, person: Optional[str], medical_companies: List[str]) -> bool:
    if person:
        return True
    return normalize_key(company) in medical_companies


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_destination(
    file_path: Path,
    cfg: Config,
) -> PlannedAction:
    name_without_ext = file_path.stem
    date_str, parts = split_filename_parts(name_without_ext)

    if not date_str:
        return PlannedAction(
            source=file_path,
            destination=cfg.review_dir / file_path.name,
            status="review",
            reason="missing or invalid date prefix (expected YYYY.MM.DD - ...)",
        )

    try:
        dt = datetime.strptime(date_str, "%Y.%m.%d")
    except ValueError:
        return PlannedAction(
            source=file_path,
            destination=cfg.review_dir / file_path.name,
            status="review",
            reason="invalid date prefix",
        )

    year = dt.strftime("%Y")
    company_raw = parts[0]
    company = sanitize_filename_component(canonical_company(company_raw, cfg.company_aliases))
    if not company:
        return PlannedAction(
            source=file_path,
            destination=cfg.review_dir / file_path.name,
            status="review",
            reason="company name is empty after normalization",
        )

    trailing_parts = [sanitize_filename_component(p) for p in parts[1:] if sanitize_filename_component(p)]
    person = extract_person(trailing_parts, cfg.medical_people)
    medical = is_medical(company, person, cfg.medical_companies)

    description_parts = trailing_parts[:]
    if person:
        description_parts = [p for p in description_parts if normalize_key(p) != normalize_key(person)]
    description = " - ".join(description_parts)

    if medical and not person:
        return PlannedAction(
            source=file_path,
            destination=cfg.review_dir / file_path.name,
            status="review",
            reason="classified medical but no person token matched medical_people map",
        )

    if medical:
        new_name_parts = [date_str, company, person]
        if description:
            new_name_parts.append(description)
        new_name = " - ".join(new_name_parts) + file_path.suffix.lower()
        destination = cfg.destination_root / year / "Medical" / person / new_name
    else:
        new_name_parts = [date_str, company]
        if description:
            new_name_parts.append(description)
        new_name = " - ".join(new_name_parts) + file_path.suffix.lower()
        destination = cfg.destination_root / year / company / new_name

    return PlannedAction(
        source=file_path,
        destination=destination,
        status="ok",
        reason="classified",
    )


def list_candidate_files(cfg: Config) -> List[Path]:
    files = []
    for item in cfg.source_dir.iterdir():
        if not item.is_file():
            continue
        if item.suffix.lower() in cfg.file_extensions:
            files.append(item)
    return sorted(files)


class TeeLogger:
    def __init__(self, log_path: Path, fallback_dir: Optional[Path] = None) -> None:
        self.log_path = log_path
        self.fallback_dir = fallback_dir
        self._fh = None

    def __enter__(self) -> "TeeLogger":
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.log_path.open("a", encoding="utf-8")
        except OSError as exc:
            if self.fallback_dir is None:
                raise
            fallback_path = self.fallback_dir / self.log_path.name
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = fallback_path.open("a", encoding="utf-8")
            self.log_path = fallback_path
            print(f"WARNING: primary log path unavailable ({exc}); using fallback log path {self.log_path}")
        self.line("")
        self.line(f"==== Run started {datetime.now().isoformat(timespec='seconds')} ====")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def line(self, message: str) -> None:
        print(message)
        if self._fh:
            self._fh.write(message + "\n")
            self._fh.flush()


def process_files(cfg: Config, dry_run: bool) -> int:
    cfg.destination_root.mkdir(parents=True, exist_ok=True)
    cfg.review_dir.mkdir(parents=True, exist_ok=True)
    log_year = datetime.now().strftime("%Y")
    log_path = cfg.destination_root / log_year / f"filestash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    fallback_dir = Path.cwd()

    with TeeLogger(log_path, fallback_dir=fallback_dir) as logger:
        logger.line(f"Config source_dir: {cfg.source_dir}")
        logger.line(f"Config destination_root: {cfg.destination_root}")
        logger.line(f"Config review_dir: {cfg.review_dir}")
        logger.line(f"Dry run: {dry_run}")

        if not cfg.source_dir.exists():
            logger.line(f"ERROR: source_dir does not exist: {cfg.source_dir}")
            logger.line(f"Log file: {log_path}")
            return 2

        index_path = cfg.destination_root / ".filing_hash_index.json"
        index = load_index(index_path)

        files = list_candidate_files(cfg)
        if not files:
            logger.line("No candidate files found.")
            logger.line(f"Log file: {log_path}")
            return 0

        planned = [build_destination(path, cfg) for path in files]

        moved = 0
        reviewed = 0
        duplicates = 0
        errors = 0

        for action in planned:
            source = action.source
            destination = action.destination
            if destination is None:
                logger.line(f"SKIP  {source.name}: no destination")
                continue

            try:
                source_hash = hash_file(source)
            except OSError as exc:
                logger.line(f"ERROR {source.name}: failed to hash source ({exc})")
                errors += 1
                continue

            known_paths = index.get(source_hash, [])
            if any(Path(p).exists() for p in known_paths):
                logger.line(f"DUP   {source.name}: identical file already indexed")
                duplicates += 1
                continue

            if action.status == "review":
                target = ensure_unique_path(destination)
                logger.line(f"REVIEW {source.name} -> {target} ({action.reason})")
                reviewed += 1
            else:
                target = ensure_unique_path(destination)
                logger.line(f"MOVE  {source.name} -> {target}")
                moved += 1

            if dry_run:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if cfg.move_files:
                    shutil.move(str(source), str(target))
                else:
                    shutil.copy2(str(source), str(target))
            except OSError as exc:
                logger.line(f"ERROR {source.name}: move/copy failed ({exc})")
                errors += 1
                continue

            target_hash = source_hash
            target_key = str(target)
            existing = index.setdefault(target_hash, [])
            if target_key not in existing:
                existing.append(target_key)

        if not dry_run:
            save_index(index_path, index)

        logger.line("")
        logger.line("Summary")
        logger.line(f"  Candidates: {len(files)}")
        logger.line(f"  Moved:      {moved}")
        logger.line(f"  To review:  {reviewed}")
        logger.line(f"  Duplicates: {duplicates}")
        logger.line(f"  Errors:     {errors}")
        logger.line(f"  Dry run:    {dry_run}")
        logger.line(f"Log file: {log_path}")

        return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FileStash: on-demand scanned document organizer for a structured archive."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON config file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without moving/copying files.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        print(f"ERROR: config file not found: {config_path}")
        return 2

    try:
        cfg = parse_config(config_path)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: invalid config ({exc})")
        return 2

    return process_files(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
