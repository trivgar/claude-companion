# Adding Outlook mail

The mail drafter reads new mail, writes replies for you to approve, and puts approved replies in your drafts folder. Gmail ships in the box. Outlook (Microsoft 365 or outlook.com) is a second provider you add by writing one file, `claude_companion/mail/outlook.py`. This page is for a Claude doing that for its owner.

## The rule

Mail cannot send. Not through a flag, not through a helper. The interface in `claude_companion/mail/__init__.py` has three methods and none of them sends, replies or forwards, and `tests/test_mail.py::test_no_source_can_send` fails if any registered source has an attribute whose name contains `send`, `reply` or `forward` (any case). Name your helpers accordingly (`make_draft`, not `create_reply`).

## The interface

```python
class MailSource(Protocol):
    name: str
    account: str
    def list_new(self, since: float) -> list[dict]: ...   # {id, thread, from, to, subject, date, snippet}
    def read(self, msg_id: str) -> dict: ...              # the same, plus "body" (plain text)
    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str: ...  # the draft's id
```

`list_new` takes epoch seconds and should leave out threads whose newest message is already from the owner. `read` returns plain text: strip HTML and quoted history. `create_draft` must return an id or raise.

## Microsoft Graph, device-code sign-in

Use plain `urllib` plus the `msal` package (`pip install msal`), so the core stays dependency-free.

1. The owner registers an app once in Microsoft Entra ("App registrations"): a public client, "Allow public client flows" on, no redirect URI. Note the application (client) id; put it in `~/.config/claude-companion/config.toml` as `outlook_client_id`. Use tenant `common` unless the owner's organization says otherwise.
2. Sign in with the device-code flow, so no browser has to run on the server:
   ```python
   app = msal.PublicClientApplication(client_id, authority="https://login.microsoftonline.com/common",
                                      token_cache=cache)
   flow = app.initiate_device_flow(scopes=["Mail.ReadWrite"])
   print(flow["message"])                      # the owner opens the URL and types the code
   result = app.acquire_token_by_device_flow(flow)
   ```
   Keep the MSAL token cache in `~/.config/claude-companion/outlook-token.json`, mode 600, and use `acquire_token_silent` afterwards. Add a `python -m claude_companion.mail outlook-login` command if you want the owner to run the sign-in once by hand.
3. **Request the `Mail.ReadWrite` scope and nothing else. Never request `Mail.Send`.** Without that permission the token cannot send even if a bug tried. `Mail.ReadWrite` is needed because creating a draft is a write; it does not allow sending.

## The three calls

- `list_new`: `GET /me/mailFolders/inbox/messages?$filter=receivedDateTime ge {iso}&$orderby=receivedDateTime desc&$top=40&$select=id,conversationId,from,toRecipients,subject,receivedDateTime,bodyPreview`. Map `conversationId` to `thread`. To drop threads the owner already answered, compare against `GET /me/mailFolders/sentitems/messages?$filter=conversationId eq '{id}'&$top=1&$orderby=sentDateTime desc` only for messages you are about to return.
- `read`: `GET /me/messages/{id}?$select=...,body` with header `Prefer: outlook.body-content-type="text"`.
- `create_draft`: Graph makes a reply draft with `POST /me/messages/{id}/createReply`, which returns a new draft message in the same conversation (this is a draft, not a sent reply). Then `PATCH /me/messages/{draft_id}` with `{"body": {"contentType": "text", "content": body}}`. The queue passes a `thread_id`, so look up the newest message in that conversation first (`GET /me/messages?$filter=conversationId eq '{thread_id}'&$orderby=receivedDateTime desc&$top=1`) and create the draft from it. Return the draft's `id`. Never call `/send` on it.

Keep message ids URL-safe before putting them in a path; Graph ids contain `=` and `-`.

## Register it

The factory receives the configured account id, so make the source constructor
accept it (for example, `def __init__(self, account: str = "owner")`) and keep
it on `self.account`. At the bottom of `claude_companion/mail/__init__.py`,
register it with that id:

```python
def _outlook(account: str) -> MailSource:
    from .outlook import OutlookSource
    return OutlookSource(account)

register("outlook", _outlook)
```

Then set this in `~/.config/claude-companion/config.toml`:

```toml
[mail_accounts.owner]
source = "outlook"
```

`voice-guide` currently asks Gmail's read tools, so do not run it for Outlook.
Build the private voice guide from the text of about 30 sent messages (or add a
Graph-reading variant) and keep it at
`~/.config/claude-companion/voice-guide.md`, mode 600.

## Tests

Stub the HTTP layer, never the real account. Cover: `list_new` maps fields and drops answered threads; `create_draft` calls `createReply` then PATCH and never any path ending in `/send`; the MSAL scopes list is exactly `["Mail.ReadWrite"]`. The existing `test_no_source_can_send` picks up your class automatically once it is registered.

## Installing the timer (either provider)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/mail-draft.service systemd/mail-draft.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now mail-draft.timer
```

The service assumes the repo is at `~/claude-companion`; edit `ExecStart` if it is elsewhere.
