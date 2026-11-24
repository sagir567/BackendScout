---
name: front2job-daily-scout
description: Check Front2Job partner employers for relevant Israeli tech roles and feed verified official jobs into TechJob Scout's daily review workflow.
---

# Front2Job Daily Scout

Use `https://front2job.com/employers` as the authoritative employer directory.
Synchronize it at run time instead of assuming the company list is unchanged.

## Workflow

1. Read the employer name and official careers link from the directory.
2. Prefer a published Greenhouse, Lever or Ashby board discovered in that link or
   the official careers page. Never guess a board token or application URL.
3. Collect listings through the read-only public board endpoint, then apply the
   normal Israel/remote, title, experience and transparent-score filters.
4. Preserve Front2Job as discovery provenance and the official employer/ATS URL
   as the application destination. Mention the Front2Job initiative in a cover
   note only when the reviewed application package includes it.
5. Keep unsupported, closed, ambiguous or unreachable employers out of the
   ready-to-apply view. Include relevant unresolved roles or source failures in
   the daily owner summary with the company, role/link when known and the exact
   blocker.

The skill is read-only. It does not approve a CV, upload personal data, accept
terms or submit an application. Delivery still requires approval of the exact
current package under the project's normal workflow.
