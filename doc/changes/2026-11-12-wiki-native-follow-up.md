# Native wiki publication and portable tests

Feedback revision 56 prompted a focused correction to Windows publication:
use extended replacement semantics, resolve OWNER_RIGHTS against the verified
object owner, and preserve the original native error through cleanup. Installer
errors expose a fixed operation name and numeric code without private paths or
exception text. Portable test entry points and deterministic DNS/address-budget
fixtures preserve the existing isolation and transport protections. These are
derived implementation repairs, not additional user requirements. Known ceiling:
the new native Windows regressions and installed MCP/account acceptance require
actual execution on an authorized Windows environment; Linux and modeled tests
do not establish native acceptance. See the [requirements](../harness/REQ__python-native-wiki.md)
and [installation guide](../../wiki-atlassian/README.md) for the contract and commands.
