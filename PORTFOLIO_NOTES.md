# Portfolio Notes

This repository intentionally abstracts a real retail implementation.

## Sanitization applied

Removed or parameterized:

- client name and brands;
- production GCP project identifiers;
- production bucket names;
- service-account names;
- dataset and table names;
- raw customer counts;
- CPF/document values;
- model artifact hashes;
- exact high-value monetary threshold;
- operational run IDs;
- source-system-specific business names.

## What remains representative

The architecture, lifecycle-rule design, historical/rolling-window separation, high-value churn methodology, ML pipeline shape, auditing approach, scheduler pattern, and deployment structure reflect the implemented solution.
