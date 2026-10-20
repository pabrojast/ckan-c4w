# Citizens4Water content administration

C4W content administrators can use `/citizens4water/admin` to review contributions,
manage visibility, edit events and permanently delete hidden content. This role
never grants CKAN/IHP-WINS sysadmin privileges or authority to approve organisation
membership requests. Existing CKAN sysadmins retain their access.

An operator can preview, apply or revoke this role for an existing active account:

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
