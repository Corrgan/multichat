# Login setup: developer apps for Twitch, Kick and YouTube

MultiChat doesn't need a login to **read** the chats. To **send and moderate**, you log in to
the respective platform. Each platform requires its own free *developer app* for that, which
you create once yourself. It gives you two values:

- **Client ID** – public, identifies your app
- **Client secret** – treat it like a password, never share it

Enter both in MultiChat under **Accounts → Set up developer apps …**, then click
**Accounts → Log in to …**. Your
browser opens, you confirm, done.

> **Important – the same redirect URL for all three platforms, exactly like this:**
>
> ```
> http://localhost:17563/callback
> ```
>
> `http` (not https), `localhost` (not 127.0.0.1), port `17563`, path `/callback`, no trailing `/`.
> During login MultiChat briefly starts a small web server on this port that receives the platform's response.

To moderate, you also need to be a **moderator** on the channel (or it's your own channel).

---

## Twitch

**Prerequisite:** two-factor authentication (2FA) is enabled for your Twitch account
([Settings → Security and Privacy](https://www.twitch.tv/settings/security)).
Reload the developer console once after enabling it.

1. Open the [Twitch developer console → Applications](https://dev.twitch.tv/console/apps) and click **Register Your Application**.
2. Fill in the form like this:

   | Field | Value |
   |---|---|
   | **Name** | anything, but unique across all of Twitch, e.g. `MultiChat-yourname`. Preferably without the word "Twitch". The name is shown in the consent window when you log in. |
   | **OAuth Redirect URLs** | enter `http://localhost:17563/callback` and click **Add** |
   | **Category** | **Chat Bot** (the category doesn't affect functionality) |
   | **Client Type** | **Confidential** – only this type gets a client secret, which MultiChat needs for the login (authorization code flow). *Public* is meant for apps without a secret and doesn't work here. |

3. Solve the captcha, click **Create**.
4. Under **Developer Applications**, click **Manage** next to your app.
5. Copy the **Client ID**. Under **Client Secret**, click **New Secret** and copy the value.
   A new secret invalidates the old one – enter the new one in MultiChat as well.

You don't configure **permissions** (scopes) in the Twitch app; MultiChat requests them at login:
`user:write:chat` (send), `moderator:manage:chat_messages` (delete messages),
`moderator:manage:banned_users` (timeout/ban/unban), `moderator:read:followers` (followage in the
user card) and `user:read:moderated_channels` (which channels you moderate – mod actions are
hidden elsewhere).

> **Logged in with a version before 0.50?** Log out and in again once (**Accounts → Twitch – log
> out**, then log in) so Twitch grants the two new permissions. Until then everything else keeps
> working; only followage and the moderator check are missing.

📖 Docs: [Twitch – Register Your App](https://dev.twitch.tv/docs/authentication/register-app) ·
[Authentication & token flows](https://dev.twitch.tv/docs/authentication/) ·
[Forum: Confidential or Public?](https://discuss.dev.twitch.com/t/what-type-of-client-to-choose/63638)

---

## Kick

**Prerequisite:** two-factor authentication (2FA) is enabled for your Kick account
(account settings → security). Without 2FA the developer section doesn't appear.

1. Open [kick.com → Settings → Developer](https://kick.com/settings/developer) and click **Create new**.
2. Fill in the form like this:

   | Field | Value |
   |---|---|
   | **Name** | anything, e.g. `MultiChat` |
   | **Description** | anything, e.g. `Personal multi-chat` |
   | **Redirect URI** | `http://localhost:17563/callback` |
   | **Enable Webhooks** | **off** – MultiChat doesn't use webhooks |
   | **Scopes** | tick exactly these (see below) |

   | Scope | used by MultiChat for |
   |---|---|
   | `user:read` | showing your Kick name after login |
   | `channel:read` | looking up the channel ID for a channel name |
   | `chat:write` | sending messages |
   | `moderation:ban` | timeouts and bans |
   | `moderation:chat_message:manage` | deleting messages |

   Leave the other scopes (e.g. `streamkey:read`, `channel:write`, `events:subscribe`) **unticked** – MultiChat doesn't need them.
3. Click **Create App**, then copy the **Client ID** and **Client Secret**.

If a scope is missing from the app, the login or the respective action fails later – check the ticks then.

📖 Docs: [Kick – app setup](https://github.com/KickEngineering/KickDevDocs/blob/main/getting-started/kick-apps-setup.md) ·
[Kick – list of all scopes](https://github.com/KickEngineering/KickDevDocs/blob/main/scopes/scopes.md)
(source of the official Kick developer docs)

---

## YouTube (Google Cloud)

Google is the most involved of the three. You set up a small "app" in the Google Cloud Console
once; it costs nothing and needs no credit card. Plan about 10 minutes.

> **Use one Google account for everything** if you can: the account that owns the YouTube channel
> (or moderates it) creates the project, is added as test user and logs in in MultiChat.
> If you use different accounts, the account you log in with **must** be a test user (step 3).

The console may be shown in your language; the menu items are named equivalently.

### Step 1 – Create a project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project picker at the top left (next to "Google Cloud") → **New project** →
   name e.g. `multichat` → **Create**.
3. Make sure the new project is **selected** in the project picker before you continue –
   everything below happens inside it.

### Step 2 – Enable the YouTube API

1. Open [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
   (check at the top that your project is selected).
2. Click **Enable**. If the button says **Manage**, it is already enabled.

> Without this, the login works, but sending and moderating fail with
> *"YouTube Data API v3 has not been used in project … or it is disabled"*.

### Step 3 – Consent screen ("Google Auth Platform")

Menu **☰ → Google Auth Platform** (older name: *APIs & Services → OAuth consent screen*).
If you see **Get started**, click it and fill in:

| Page | What to enter |
|---|---|
| **App information** | App name `MultiChat`, user support email: your address |
| **Audience** | **External** (the only option for personal Google accounts) |
| **Contact information** | your email address |
| **Finish** | tick the agreement → **Continue** → **Create** |

Then, still in Google Auth Platform:

1. **Audience** (left) → section **Test users** → **+ Add users** → enter the Google account you
   will log in with in MultiChat → **Save**.
   > This is the most common mistake. Without this entry Google shows
   > **"Access blocked: MultiChat has not completed the Google verification process"**
   > (Error 403: access_denied) – even for the account that created the project.
2. **Data Access** (left) → **Add or remove scopes** → in the box *Manually add scopes* paste
   `https://www.googleapis.com/auth/youtube.force-ssl` → **Add to table** → **Update** → **Save**.
   (Optional, but then the console shows which permission the app uses.)

Leave the **publishing status** on **Testing** for now (see "Good to know" for the 7-day limit).

### Step 4 – Create the OAuth client (client ID + secret)

**Google Auth Platform → Clients** (left) → **+ Create client**:

| Field | Value |
|---|---|
| **Application type** | **Web application** – *not* "Desktop app" |
| **Name** | anything, e.g. `MultiChat` |
| **Authorized JavaScript origins** | leave empty |
| **Authorized redirect URIs** | **+ Add URI** → `http://localhost:17563/callback` |

> Type the redirect URI exactly like this: `http` (not https), `localhost` (not 127.0.0.1), port
> `17563`, path `/callback`, no slash at the end. A typo leads to **Error 400: redirect_uri_mismatch**.

Click **Create**. A window shows the **client ID** and the **client secret** – copy both now (or
download the JSON). The secret is only shown in full at this moment; if you missed it, open the
client and use **Add secret**.

### Step 5 – Enter it in MultiChat and log in

1. MultiChat → **Accounts → Set up developer apps …** → paste client ID and secret into the
   YouTube fields → **OK**.
2. **Accounts → Log in to YouTube …** – your browser opens Google's login.
3. Choose the account you added as test user.
4. Google warns **"Google hasn't verified this app"**. That is expected for your own app:
   click **Continue** – or, if there is no such button, the small link **Advanced** (bottom left)
   → **Go to MultiChat (unsafe)**.
5. Allow access ("Manage your YouTube account"). The browser shows **Logged in ✓** – back to
   MultiChat. The status bar says *Logged in to YouTube*.

### Good to know

- **Login expires after 7 days** while the publishing status is *Testing* – Google's rule for
  test apps. Just log in again. To avoid it: **Audience → Publish app** (status *In production*).
  No verification by Google is needed for personal use; you'll keep seeing the
  "hasn't verified this app" warning at login, confirm it as in step 5.
- **Which account?** You can only send in chats of live streams, and moderate (delete, timeout,
  ban) only in channels where the logged-in account is the **owner or a moderator**.
- **Quota:** sending and moderating use the YouTube API quota (10,000 units per day and project –
  plenty for normal moderation). Reading the chat and the video don't use the API.

### YouTube troubleshooting

| What you see | Cause and fix |
|---|---|
| **Access blocked: … has not completed the Google verification process** / Error 403: access_denied | The account isn't a test user → step 3.1. Check it's the account you chose in the browser. |
| **Error 400: redirect_uri_mismatch** | Redirect URI not exactly `http://localhost:17563/callback`, or the client type isn't *Web application* → step 4. Changes can take a few minutes to apply. |
| **Error 401: invalid_client** / "The OAuth client was not found" | Client ID or secret mistyped (spaces?) or from another project → copy them again (step 4) and re-enter (step 5.1). |
| "Google hasn't verified this app" without a continue button | Click **Advanced** (small link bottom left) → **Go to MultiChat (unsafe)**. |
| Login works, sending/moderating fails with *"… has not been used in project … or it is disabled"* | The YouTube Data API isn't enabled in this project → step 2. |
| Sending fails with *"forbidden"* / *"The caller does not have permission"* | The account isn't owner/moderator of the channel, or the chat is restricted (members only, slow mode). |
| *No active live chat for this video* | The channel isn't live, or the stream has its chat turned off. |
| Worked before, now *"session expired – please log in again"* | 7-day limit of test apps → log in again, or publish the app (Good to know). |
| *Port 17563 in use* | Another login is still waiting – close that browser tab, wait a minute, try again. |

📖 Docs: [YouTube Data API – OAuth 2.0 for web server apps](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps) ·
[Manage the OAuth consent screen / test users](https://support.google.com/cloud/answer/15549945) ·
[Testing vs. production](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)

---

## Login troubleshooting

| Message / behaviour | Cause |
|---|---|
| Browser shows "redirect_uri mismatch" / "invalid redirect" | the redirect URL in the developer app differs – enter exactly `http://localhost:17563/callback` |
| "Port 17563 in use" | another login is still running or another program uses the port – wait a moment or restart MultiChat |
| Google: "Access blocked" / "access_denied" | the Google account is missing under **Audience → Test users** |
| Twitch: login fails at the end (e.g. "invalid client") | Client Type is *Public* instead of *Confidential* – public apps have no secret; create a new app with *Confidential* |
| Sending works, moderating doesn't | you're not a moderator on the channel |
| Kick: login or an action is rejected | a required scope isn't ticked in the Kick app |
| Sending fails with "rejected the message (… 403 …)" | the channel restricts its chat (followers only – often with a minimum follow time –, subscribers only, slow mode, verified accounts only); check in the browser whether you can write there. If it fails in your own channel as well, `chat:write` (Kick) is missing in the developer app – tick it, then log out and in again |
| "session expired – please log in again" | token expired (with Google in Testing status after 7 days) – log in again |

The log always has more details: **Help → Copy log**.
