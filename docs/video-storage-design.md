# Video storage and future sharing

## Decision

Do **not** store raw video in Tiger/TimescaleDB. Store camera media in a private Vultr Object Storage bucket;
store only metadata, audit data and time indexes in Timescale (`session_media_assets`, migration 006).

```text
Camera → Media relay → 2–6 second H.264 CMAF/HLS segments → private object storage
                                  │
                                  └→ Timescale: session, start_ms, duration, checksum, consent, expiry
```

This keeps database growth predictable while still supporting a query such as “show the flagged rep at 04:21”.
The mesh/joint stream remains compact Timescale data and can be replayed beside the matching video segments.

## Object layout

Use a private bucket and immutable keys:

```text
prod/sessions/<session-id>/camera/master.m3u8
prod/sessions/<session-id>/camera/000001.m4s
prod/sessions/<session-id>/mesh/model.glb              # optional static mesh only
```

Do not make this bucket public. The application checks patient/therapist ownership, consent and `patient_visible`
before issuing a short-lived, read-only pre-signed URL for a playlist or clip. The browser/iOS app never receives
S3 credentials. Uploading uses multipart upload for resilience; a segment is marked `ready` only after its checksum
and size are verified.

## Retention and sharing

Raw camera video starts **off**. Before recording, capture the patient's explicit consent and a `retain_until`
timestamp. A therapist may mark a consented asset `patient_visible`; the patient then sees only those assets, never
another patient's stream. Keep an audit event for consent, share, revoke, download and deletion.

Recommended defaults to confirm with clinical/legal policy:

| Item | Default |
|---|---|
| Live video | WebRTC only; no storage |
| Saved review clip | explicit consent; 30 days |
| Full-session raw video | disabled unless policy requires it |
| Pose/reps/flags | existing clinical retention policy |

Lifecycle rules in the bucket delete expired prefixes; the application then marks the corresponding database rows
`deleted`. Run a daily reconciler both ways so an object deletion never becomes an available-looking review link.

## Vultr deployment

Vultr Object Storage is S3-compatible and supports multipart upload, pre-signed URLs, object versioning and bucket
lifecycle rules. Put the bucket in the appropriate data-residency region, use a dedicated least-privilege service key,
and configure lifecycle expiry from `retain_until` retention classes. Keep video storage separate from application
logs and backups. See the [Vultr Object Storage security guidance](https://docs.vultr.com/platform/security-best-practices/vultr-object-storage)
and [S3 compatibility matrix](https://docs.vultr.com/products/storage/object-storage/s3-compatibility-matrix).
