# Citizens4Water content administration

C4W content administrators can use `/citizens4water/admin` to review contributions,
manage visibility, edit events and permanently delete hidden content. This role
never grants CKAN/IHP-WINS sysadmin privileges or authority to approve organisation
membership requests. Existing CKAN sysadmins retain their access.

System administrators can open **Content administration → Administrators**
(`/citizens4water/admin/administrators`) to search existing active accounts by
username, name or email, and select **Add administrator**. The same screen lists
current assignments and offers **Remove administrator**. It is also linked from
the account menu and the missing-notification-recipient notice in Moderation.

Adding a role grants C4W content access and includes the active account in review
notifications when it has an email address. The screen identifies accounts that
cannot receive mail. Removing a C4W role takes effect immediately; a system
administrator retains their global content access. C4W administrators cannot
assign roles to other users. Account creation, email and global roles are unchanged.

Operators can also preview, apply or revoke this role from the command line:

```sh
ckan -c production.ini c4w content-admin USERNAME --actor EXISTING_SYSADMIN
ckan -c production.ini c4w content-admin USERNAME --actor EXISTING_SYSADMIN --apply
ckan -c production.ini c4w content-admin USERNAME --actor EXISTING_SYSADMIN --revoke --apply
```

Role assignments are stored in `c4w_portal_admin`, outside public profiles and
registration forms. Neither self-service profile updates nor C4W moderation can
grant this role.

## Delete hidden content

1. Open the appropriate inventory and choose **Hidden**.
2. Select **Delete**. Review the item identifier, dependencies and dataset files.
3. Resolve any incoming links or pending drafts listed on the confirmation page.
   Related content is never deleted automatically. Queued/processing datasets
   must finish before they can be removed.
4. Type the exact identifier and select **Permanently delete**.

The preview is read-only. Deletion rechecks permissions, visibility, dependencies
and the confirmation version in the same transaction. Drafts, outgoing links,
facet terms and dashboard bundles are removed with the item. Exclusive dataset
files are cleaned up after commit; shared images and external URLs are retained.
Storage failures appear as **Retry file cleanup** in the inventory. The durable
`c4w_deletion` audit records the actor and item identifier, and retains pending
cleanup names. Its legacy IDs also prevent subsequent imports from recreating
items deliberately deleted by an administrator.

The action interfaces are `c4w_entity_delete_preview` and `c4w_entity_delete`,
with `entity_type` and the canonical UUID `id`. Deletion additionally requires
`version` from the preview and `confirm_slug`. `c4w_deletion_list` lists pending
storage cleanup; `c4w_deletion_cleanup` retries it using the deletion audit ID.
All four actions require C4W content administration.

## Catalogue filters and attribution

Individual filter groups start collapsed. Active selections remain visible above
the results, and expand/collapse-all is available with JavaScript. Native
individual disclosures and filter links also work without JavaScript. On mobile,
the filters button precedes results.

The footer uses the supplied Scivil/RVO Society image, the horizontal C4W logo and
Flanders at equal displayed heights (56 px desktop, 44 px mobile), with the exact
Flanders UNESCO Trust Fund acknowledgement supplied in the mock-up.

## Release checks

Run `bash scripts/run-ckan-tests.sh`, restore a fresh backup in isolation, and
verify the additive schema upgrade and concurrent deletion/reference writes.
Then verify DEV in a real browser with a C4W-only administrator and an ordinary
member. Promote with the official production release script and a pinned image
digest, preserving environment-specific headers and configuration.

Account recovery uses the existing CKAN password-reset mechanism. Test it with
a disposable account first; never capture a real user's password or reset link
in release notes, source control or public logs.

## Editorial workflow (DEV, October 2026)

**Content administration** (`/citizens4water/admin`) opens the eight inventories.
Each item has **Edit** and **View**. Saving an existing published item takes effect
immediately; its URL, owner and Hide/Unhide setting remain unchanged. Data editing
covers descriptive metadata only and leaves uploaded measurements, column mapping
and dashboard generations intact. Forms reject stale versions instead of silently
overwriting another editor.

**Moderation** (`/citizens4water/admin/moderation`) is a separate queue for submitted
new contributions and changes. Hidden published items do not appear simply because
they are hidden. Project/news/event reviews compare the public and proposed values.
Approve publishes the proposal; Request corrections and Reject require a reason.
Authors can edit and resubmit rejected contributions. An open proposal must be
resolved before direct administrative editing; another author's draft is protected.

**My submissions** includes created/managed content and revisions the user has
created, edited or submitted, including revisions of imported projects. It covers
all contribution types. A draft only enters moderation when submitted.

Submission notifications are addressed to active users explicitly assigned the
C4W content-administrator role. Global CKAN sysadmins are not automatically emailed.
If there are no recipients, or SMTP fails, the request remains in the queue and
an explanatory notice is displayed. Decisions are addressed to the submitter,
including when the original imported creator differs. Real email delivery should
be checked separately from a captured-mail integration test.

The administrative actions are `c4w_admin_entity_edit_show`,
`c4w_admin_entity_update` and `c4w_admin_entity_review`. They accept a whitelisted
`section` and canonical UUID `id`; writes also require the current `version` from
the read action. Update accepts only explicitly declared editorial fields, optional
image uploads and `remove_<image>` flags. Review accepts `decision` (`approve`,
`changes_requested`, `rejected`) and `note`. Existing actions/routes remain valid.

### Imported content and editors

Participant counts can be exact numbers, ranges or descriptive text. Recognized
legacy language names are normalized for editing; unknown imported selections
remain available. Unchanged legacy metadata does not block an unrelated edit,
and text is never truncated. Changed fields must satisfy validation; markup and
links pass through the existing sanitizer. Descriptive fields offer Insert/edit
link and Remove link; URLs must use HTTP, HTTPS or mailto. Rich text counters count
visible text, with 15,000 characters for descriptions and 100,000 for news bodies.

Images have a Delete button beside their preview. A new upload replaces that
preview. Database edits and uploads are saved together, with failed staged uploads
cleaned up; existing referenced images are retained. Contributor proposals leave
the approved images public until approval.

The footer includes the existing WINS UNESCO-IHP artwork, with its proportions
preserved, alongside the established Citizens4Water, Scivil and Flanders credits.

### Upgrade

Before rollout, back up DEV and verify restoration. Add nullable `submitted_by`,
`modified_by` and `notification_status` columns to both revision tables with
`c4w init-db`; the upgrade is additive and idempotent. Existing rows require no
reimport. Validate imported records against an isolated restored database before
release. Rollback uses the preceding image and retains these compatible columns.
