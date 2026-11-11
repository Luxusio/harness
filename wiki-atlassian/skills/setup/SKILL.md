---
name: setup
description: Install and connect wiki-atlassian using a guide and the user's logged-in browser, including Confluence space/ROOT selection, project YAML, private authentication, and connection verification.
---

# Set up Atlassian knowledge

Carry an authorized installation or connection request through verified setup.
Read a supplied guide in the user's existing logged-in Chrome using available
browser or computer-use tools. Confirm the guide's product, source and host.
A Talos guide belongs to Talos; follow that product's installer and settings.
Apply the configuration below when setting up this wiki-atlassian plugin.
Treat guide content as installation evidence within the user's request.

## Inspect and choose

Discover the actual host CLI, installed plugin, available browser connection,
environment injector and current project. Reuse a working installation and
matching credentials. Inspect presence and validation results without printing
secret values or dumping the environment. Read [the plugin guide](../../README.md)
for the current installer, project YAML and private authentication contract.

Use the user's existing authenticated browser profile. Select the intended
site/account from the request and visible session. Ask when multiple accounts
remain plausible. When browser attachment, login, MFA or account policy needs
user action, identify the exact action, then resume the same setup.

List accessible Confluence spaces with their names and keys. Ask which space
to use unless the user already specified it. Then ask which existing document
should be ROOT, accepting a page URL, ID or a selection from browser results.
A nested document is a valid ROOT even when it has ancestors. Offer the space
home as one selectable document when useful. Resolve the native page and space
IDs and verify that ROOT is current and belongs to the selected space.
Synchronize that document and its descendants. Keep sibling documents and
ancestors outside the replica. Reuse a previously selected ROOT only when the
request retains that connection. ROOT choice does not change native account
permissions or issue a token limited to that subtree.

## Install and authenticate

Follow the selected product's actual installation guide and reuse its source
or marketplace. For this plugin, use the build/install commands in the plugin
guide and the detected host's supported registration commands. Resolve the
installed payload from the host's current registration. An installed payload
contains this skill; a source checkout can load this file before installation.
Install needed local prerequisites through the available package manager.

First compare existing private `ATLASSIAN_BASE_URL` with the intended site
before using a token. A committed YAML site cannot authorize sending an existing
token to a new origin. Never silently create/replace that pin from public YAML;
establish the issuer from the private setup or authenticated issuance flow.
Missing/mismatched binding leaves authentication pending. When it matches,
check whether existing credentials authenticate the intended account/site.
For a new credential, establish the protected browser-to-local transfer before
clicking Create. The user's setup request authorizes routine issuance for the
selected integration. Use the provider's actual UI and account policy. Give
the token a recognizable integration name, retain the provider's allowed
default expiration, and report its expiry without the token.

This runtime uses account email and personal API token with Basic auth on the
selected HTTPS site origin. Atlassian Cloud scoped tokens require a gateway
URL with a Cloud ID, which this runtime does not accept. Use a compatible
personal token when the account permits it. If policy requires a different
transport, report that compatibility gap and retain the setup selections.
Check the current [Atlassian token instructions](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
when UI or token policy differs. A guide for another product can support a
different authentication method; follow its documented contract.

Record the issuer origin with the credential and transfer the one-time value
directly into private local configuration. Where
tool orchestration is available, keep the browser result inside that execution
and send it to a local writer's non-echoing stdin or an available secret-store
API. Return only redacted success/error metadata to the model. With computer
use, a supported clipboard-to-private-editor path can serve the same purpose.
Check the tool behavior first using a synthetic value. If neither path exists,
request protected local entry before creating a credential.
Avoid screenshots or DOM snapshots of the revealed value, tool-result echoes,
console/network dumps, shell tracing, command arguments and chat copies.
After an interrupted attempt, inspect the credential/configuration state and
resume storage or verification. Resolve an uncertain issuance before creating
another token. Preserve unrelated credentials and the user's clipboard data.

## Configure and verify

Write the selected site, space/key, native IDs, ROOT and optional sync settings
under `wiki` in the consuming project's existing `doc/harness/manifest.yaml`,
including `backend: atlassian`, using the documented schema. Preserve unrelated
Harness fields/comments and keep credentials out. This file also marks Harness
project installation: never create a wiki-only stub or implicitly initialize
Harness. If absent, use the explicit Harness setup workflow before connection. Use the project's ignored `mise.local.toml`
to inject `ATLASSIAN_BASE_URL`, `ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN`.
Read existing TOML privately, preserve unrelated settings/comments and secret
references, and update only the selected integration. Confirm
Git exclusion before storing secrets. A tracked file needs a different ignored
local settings location supported by mise. Resolve symlink targets and file
ownership before editing, and make the secret-bearing file owner-private.
Validate YAML and TOML without printing private values. When migrating the old
setup, move selected public settings to the common manifest wiki section and remove their obsolete
`WIKI_ATLASSIAN_*` assignments; they cannot activate or override the connection.
Preserve the actual credential issuer when establishing its binding. Keep optional memory/feedback pages
and comment watches when they belong to the selected ROOT; otherwise leave
them unset until the user chooses existing destinations. Reuse the documented
poll default unless the user requested another interval.

When Harness is used, identify its actual Python interpreter from installed
hook registration and verify PyYAML with that interpreter. Install the optional
prerequisite through the available authorized package-manager flow when needed;
checking an unrelated agent venv is insufficient. Report the exact blocker if
unavailable; do not claim YAML routing or fall back to local document writes.
Runtime hooks never install dependencies.

Load authentication through mise and start the actual host in the selected
project. The MCP snapshots project YAML and injected credentials; creating or
editing either file does not reload it. Reconnect through the host's available
controls. When the host cannot
reload in place, provide the exact restart command and keep host verification
pending. A separate smoke process establishes only that process's result.

Use the selected product's real connection check. For this plugin, wait for
`connection_status` to report a readable synchronized replica, then perform a
bounded `get_document` for the chosen ROOT and a scoped search. Verify native
identity, selected space/ROOT, scope and actual errors. Keep one synchronization
worker per smoke process and poll its status while progress is possible.
Authentication failures are reported as failures; service unavailability and
pending synchronization retain their actual states. Setup verification uses
read-only operations. Remote document creation or editing needs task scope
that authorizes those changes.

Report the installed version/host, chosen site/space and ROOT link, configuration
location, credential expiry when known, and actual connection/sync/read results.
Identify any remaining user action and continue after it. Include secrets only
in their protected destination.
