# Notion Setup Worksheet

Use this file while setting up Notion. Fill the TODO values as you go.

## Step 1: Create The Workspace Page

- [ ] Open Notion.
- [ ] Create a new page named `BackendScout`.
- [ ] Add a short note in that page: `Job search agent tracker`.

TODO:

```text
Notion page URL:
```

## Step 2: Create The Applications Table

Inside the `BackendScout` page:

- [ ] Type `/table`.
- [ ] Choose `Table - Full page` or `Table - Inline`.
- [ ] Name it `Applications`.

TODO:

```text
Applications table URL:
```

## Step 3: Add Properties

Create these properties exactly, including capitalization.

| Done | Property | Type |
| --- | --- | --- |
| [ ] | Role | Title |
| [ ] | Company | Text |
| [ ] | Status | Status |
| [ ] | Source | Text |
| [ ] | Source URL | URL |
| [ ] | Location | Text |
| [ ] | Remote Policy | Text |
| [ ] | Employment Type | Text |
| [ ] | Salary | Text |
| [ ] | Match Score | Number |
| [ ] | Required Skills | Multi-select |
| [ ] | Years Experience | Text |
| [ ] | Match Reason | Text |
| [ ] | Description | Text |
| [ ] | Discovered At | Date |

## Step 4: Add Status Options

Add these options to the `Status` property:

- [ ] `found`
- [ ] `digest_sent`
- [ ] `approved_to_tailor`
- [ ] `cv_drafted`
- [ ] `approved_to_submit`
- [ ] `submitted`
- [ ] `recruiter_reply`
- [ ] `interview`
- [ ] `rejected`
- [ ] `offer`
- [ ] `closed`

## Step 5: Create A Notion Integration

- [ ] Go to `https://www.notion.so/profile/integrations`.
- [ ] Create a new internal integration.
- [ ] Name it `BackendScout`.
- [ ] Copy the integration secret.

Do not paste the real secret into this tracked file.

TODO:

```text
Integration created: yes/no
Secret stored in .env: yes/no
```

## Step 6: Share The Table With The Integration

Open the `Applications` table/page in Notion:

- [ ] Click the three-dot menu or `Share`.
- [ ] Choose connections/integrations.
- [ ] Add the `BackendScout` integration.

TODO:

```text
Integration has access to Applications: yes/no
```

## Step 7: Find The Data Source ID

Open the `Applications` table as a full page and copy the URL.

TODO:

```text
Full Applications URL:
Data source ID candidate:
```

The ID is usually the long UUID-like value in the URL. Keep it private enough
for ordinary use, but it is less sensitive than the integration secret.

## Step 8: Create Your Local .env

Create `.env` from `.env.example` and fill these values:

```bash
NOTION_API_KEY=TODO_PASTE_SECRET_HERE
NOTION_API_VERSION=2026-03-11
NOTION_APPLICATIONS_DATA_SOURCE_ID=TODO_PASTE_DATA_SOURCE_ID_HERE
CV_ARCHIVE_ROOT=/Users/sagi/Documents/CV
```

Do not commit `.env`.

## Checkpoint

When you finish, tell Codex:

```text
I created the Notion table and filled .env. Add a command to verify the Notion connection.
```
