# FileStash

FileStash is an on-demand document organizer for scanned files.

It moves files from an inbox into a long-term archive structure:

- Standard: `Files/YYYY/Company/YYYY.MM.DD - Company - Description.ext`
- Medical: `Files/YYYY/Medical/Person/YYYY.MM.DD - Company - Person - Description.ext`
- Review queue: `Files/_Needs Review`

It also:

- Writes a run log to `Files/YYYY/filestash_YYYYMMDD_HHMMSS.log`
- Detects duplicates using SHA-256 hashes
- Stores hash history in `Files/.filing_hash_index.json`

## Requirements

- Python 3.9+

## Setup

1. Copy the example config:

```bash
cp ./filestash_config.example.json \
   ./filestash_config.json
```

2. Edit `./filestash_config.json` with your real paths and mappings.

## Run

Dry run:

```bash
python3 filestash.py \
  --config ./filestash_config.json \
  --dry-run
```

Live run:

```bash
python3 filestash.py \
  --config ./filestash_config.json
```

## Config Keys

- `source_dir`: Inbox folder to scan.
- `destination_root`: Archive root (the `Files` folder).
- `review_dir`: Fallback folder for uncertain files.
- `file_extensions`: Allowed extensions to process.
- `move_files`: `true` moves files, `false` copies files.
- `company_aliases`: Normalize company names.
- `medical_people`: Person token normalization for medical routing.
- `medical_companies`: Company names treated as medical if person token is present.


