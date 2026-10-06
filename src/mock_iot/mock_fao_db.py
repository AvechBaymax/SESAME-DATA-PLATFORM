#!/usr/bin/env python3
"""
Upload static sesame reference data to MinIO:
  - FAO-56/FAO-33 crop parameters (Kc, stage lengths, root depth, p, Ky)
  - Local practice from the Binh Thuan cultivation guide (sowing windows,
    spacing, fertilizer schedule by DAS, seed/grain standards)

Usage:
  python -m mock_iot.mock_fao_db

Object keys:
  fao_reference/crop=sesame/fao_56_33_params.json
  agronomy_reference/crop=sesame/binh_thuan_black_2shell.json
"""
import argparse
from datetime import datetime, timezone

from common.config import add_minio_args
from common.crop import BINH_THUAN_PRACTICE, SESAME_FAO
from common.storage import ensure_bucket, landing_key, s3_client_from_args, upload_json


def main(argv=None):
    ap = argparse.ArgumentParser(description="Upload sesame reference parameters to MinIO")
    add_minio_args(ap)
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc).isoformat()
    documents = {
        landing_key("fao_reference", "fao_56_33_params.json", crop="sesame"):
            {**SESAME_FAO, "updated_at": now},
        landing_key("agronomy_reference", "binh_thuan_black_2shell.json", crop="sesame"):
            {**BINH_THUAN_PRACTICE, "updated_at": now},
    }

    s3 = s3_client_from_args(args)
    ensure_bucket(s3, args.bucket)
    for key, payload in documents.items():
        print(f"Uploaded {upload_json(s3, args.bucket, key, payload, indent=2)}")


if __name__ == "__main__":
    main()
