# order-center migrations

- `0001_order_center`: creates the eight order-owned tables only.
- `0002_order_parse_integrity`: persists `parse_status`/creation time, allows
  `REVIEW_REQUIRED` snapshots, and records buyer-asset source URLs while keeping
  the actual ZIP attachment reference optional.

No material-platform table belongs in this migration chain.
