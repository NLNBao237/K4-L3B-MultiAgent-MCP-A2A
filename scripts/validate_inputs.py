#!/usr/bin/env python3
"""
Day09 L3B Input Validation Script
Validates all input case files against expected schema.

Usage:
    python scripts/validate_inputs.py [--dir INPUTS_DIR] [--verbose] [--summary]

Author: Team K4-L3B
Stage: Giai đoạn 1 - Infrastructure Setup
"""

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


@dataclass
class ValidationResult:
    """Result of validating a single case file."""
    case_id: str
    valid: bool
    errors: list[str]
    warnings: list[str]


class InputValidator:
    """Validates Day09 L3B input case files."""

    # Required top-level fields
    REQUIRED_FIELDS = {
        "case_id", "opened_at", "customer_request",
        "policy_version", "candidate_order_ids"
    }

    # Required customer_request fields
    CUSTOMER_REQUEST_REQUIRED = {"language", "message", "claims"}

    # Required claim fields
    CLAIM_REQUIRED = {"claim_id", "topic"}

    # Investigation scope fields
    INVESTIGATION_SCOPE_FIELDS = {
        "include_customer_history", "include_product_context", "require_independent_verification"
    }

    # Case ID pattern: L3B_CASE_XXX
    CASE_ID_PATTERN = "L3B_CASE_"
    CASE_ID_REGEX = r"^[A-Z0-9][A-Z0-9_-]{2,63}$"

    # Supported languages
    SUPPORTED_LANGUAGES = {"vi", "en", "pt", "es"}

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.results: list[ValidationResult] = []

    def validate_case(self, case_data: dict, file_path: Path) -> ValidationResult:
        """Validate a single case file."""
        errors = []
        warnings = []

        # 1. Check required fields
        missing_fields = self.REQUIRED_FIELDS - set(case_data.keys())
        if missing_fields:
            errors.append(f"Missing required fields: {missing_fields}")

        # 2. Validate case_id
        case_id = case_data.get("case_id", "")
        if not case_id.startswith(self.CASE_ID_PATTERN):
            errors.append(f"case_id must start with '{self.CASE_ID_PATTERN}'")

        # 3. Validate opened_at (ISO format)
        opened_at = case_data.get("opened_at", "")
        if not self._is_valid_iso_date(opened_at):
            errors.append(f"opened_at is not valid ISO format: {opened_at}")

        # 4. Validate customer_request
        customer_request = case_data.get("customer_request", {})
        if not customer_request:
            errors.append("customer_request is empty or missing")
        else:
            # Check language
            language = customer_request.get("language", "")
            if language not in self.SUPPORTED_LANGUAGES:
                warnings.append(f"Unsupported language: {language}")

            # Check message
            message = customer_request.get("message", "")
            if not message or len(message) < 10:
                warnings.append("customer_request.message is too short or empty")

            # Check claims
            claims = customer_request.get("claims", [])
            if not claims:
                errors.append("customer_request.claims is empty")
            else:
                for i, claim in enumerate(claims):
                    claim_missing = self.CLAIM_REQUIRED - set(claim.keys())
                    if claim_missing:
                        errors.append(f"Claim {i}: missing fields {claim_missing}")

                    topic = claim.get("topic", "")
                    if not topic:
                        warnings.append(f"Claim {i}: empty topic")

        # 5. Validate policy_version
        policy_version = case_data.get("policy_version", "")
        if not policy_version.startswith("EC_POLICY_"):
            warnings.append(f"Unusual policy_version: {policy_version}")

        # 6. Validate candidate_order_ids
        candidate_order_ids = case_data.get("candidate_order_ids", [])
        if not candidate_order_ids:
            errors.append("candidate_order_ids is empty")
        else:
            if not isinstance(candidate_order_ids, list):
                errors.append("candidate_order_ids must be a list")
            elif len(candidate_order_ids) > 10:
                warnings.append(f"Too many candidate_order_ids: {len(candidate_order_ids)}")

        # 7. Validate investigation_scope (optional but recommended)
        investigation_scope = case_data.get("investigation_scope", {})
        if investigation_scope:
            for key in investigation_scope:
                if key not in self.INVESTIGATION_SCOPE_FIELDS:
                    warnings.append(f"Unknown investigation_scope field: {key}")

        # 8. Check for unexpected fields
        known_fields = {
            "case_id", "opened_at", "customer_request", "policy_version",
            "candidate_order_ids", "investigation_scope", "customer_unique_id_hint"
        }
        unexpected = set(case_data.keys()) - known_fields
        if unexpected:
            warnings.append(f"Unexpected fields: {unexpected}")

        return ValidationResult(
            case_id=case_id,
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    def _is_valid_iso_date(self, date_str: str) -> bool:
        """Check if string is valid ISO 8601 date."""
        if not date_str:
            return False
        # Basic check for ISO format: YYYY-MM-DDTHH:MM:SS
        return "T" in date_str and ("-03:00" in date_str or "+00:00" in date_str or "Z" in date_str)

    def validate_directory(self, input_dir: Path) -> tuple[int, int, int]:
        """
        Validate all JSON files in directory.
        Returns: (total, valid, invalid)
        """
        json_files = list(input_dir.glob("L3B_CASE_*.json"))

        if not json_files:
            print(f"⚠️  No L3B_CASE_*.json files found in {input_dir}")
            return 0, 0, 0

        print(f"📋 Found {len(json_files)} case files")

        valid_count = 0
        invalid_count = 0
        error_count = 0

        for json_file in sorted(json_files):
            try:
                with open(json_file, encoding="utf-8") as f:
                    case_data = json.load(f)

                result = self.validate_case(case_data, json_file)
                self.results.append(result)

                if result.valid:
                    valid_count += 1
                    if self.verbose:
                        print(f"  ✅ {result.case_id}")
                else:
                    invalid_count += 1
                    error_count += len(result.errors)
                    print(f"  ❌ {result.case_id}")
                    for error in result.errors:
                        print(f"      ✗ {error}")

                # Print warnings in verbose mode
                if self.verbose and result.warnings:
                    for warning in result.warnings:
                        print(f"      ⚠ {warning}")

            except json.JSONDecodeError as e:
                invalid_count += 1
                print(f"  ❌ {json_file.name}: Invalid JSON - {e}")
            except Exception as e:
                invalid_count += 1
                print(f"  ❌ {json_file.name}: Error - {e}")

        return len(json_files), valid_count, invalid_count

    def print_summary(self):
        """Print validation summary."""
        total = len(self.results)
        valid = sum(1 for r in self.results if r.valid)
        invalid = total - valid
        total_errors = sum(len(r.errors) for r in self.results)
        total_warnings = sum(len(r.warnings) for r in self.results)

        print("\n" + "=" * 60)
        print("📊 VALIDATION SUMMARY")
        print("=" * 60)
        print(f"  Total files:     {total}")
        print(f"  ✅ Valid:        {valid}")
        print(f"  ❌ Invalid:      {invalid}")
        print(f"  Errors:         {total_errors}")
        print(f"  Warnings:       {total_warnings}")
        print("=" * 60)

        if invalid > 0:
            print("\n⚠️  INVALID CASES:")
            for r in self.results:
                if not r.valid:
                    print(f"  - {r.case_id}")
                    for error in r.errors:
                        print(f"      {error}")

        return invalid == 0


def main():
    parser = argparse.ArgumentParser(
        description="Validate Day09 L3B input case files"
    )
    parser.add_argument(
        "--dir", "-d",
        type=Path,
        default=Path("inputs"),
        help="Input directory containing case files (default: inputs)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all files, not just errors"
    )
    parser.add_argument(
        "--summary", "-s",
        action="store_true",
        help="Show summary after validation"
    )

    args = parser.parse_args()

    # Resolve input directory
    if not args.dir.is_absolute():
        args.dir = Path(__file__).parent.parent / args.dir

    print(f"\n🔍 Validating input files in: {args.dir}")
    print("-" * 60)

    validator = InputValidator(verbose=args.verbose)
    total, valid, invalid = validator.validate_directory(args.dir)

    if args.summary or total == 0:
        success = validator.print_summary()
    else:
        success = invalid == 0
        print(f"\n{'✅ All valid!' if success else '❌ Some files failed validation'}")

    # Exit code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
