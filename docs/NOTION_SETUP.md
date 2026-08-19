# Notion Setup

BackendScout uses a Notion data source as the application tracker.

## Manual Setup

1. Create a Notion page named `BackendScout`.
2. Add a table/database named `Applications`.
3. Create an internal Notion integration.
4. Share the `Applications` database with that integration.
5. Copy the data source ID and integration secret into `.env`.

Use `docs/NOTION_SETUP_WORKSHEET.md` as a guided checklist while you do this.

## Required Environment

```bash
NOTION_API_KEY=
NOTION_API_VERSION=2026-03-11
NOTION_APPLICATIONS_DATA_SOURCE_ID=
```

## Applications Properties

Create these properties in the `Applications` data source:

| Property | Type |
| --- | --- |
| Role | Title |
| Company | Text |
| Status | Status |
| Source | Text |
| Source URL | URL |
| Location | Text |
| Remote Policy | Text |
| Employment Type | Text |
| Salary | Text |
| Match Score | Number |
| Required Skills | Multi-select |
| Years Experience | Text |
| Match Reason | Text |
| Description | Text |
| Discovered At | Date |

## Recommended Statuses

- `found`
- `digest_sent`
- `approved_to_tailor`
- `cv_drafted`
- `approved_to_submit`
- `submitted`
- `recruiter_reply`
- `interview`
- `rejected`
- `offer`
- `closed`

## Notes

Notion's public API requires the `Notion-Version` header. The current docs list
`2026-03-11` as the latest version, so BackendScout pins that version in config.
