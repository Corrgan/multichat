# Login setup: developer apps for Twitch, Kick and YouTube

MultiChat doesn't need a login to **read** the chats. To **send and moderate**, you log in to
the respective platform. Each platform requires its own free *developer app* for that, which
you create once yourself. It gives you two values:

- **Client ID** – public, identifies your app
- **Client secret** – treat it like a password, never share it

Enter both in MultiChat under **„Konten → Entwickler-Apps einrichten …“** (Accounts → Set up
developer apps), then click **„Konten → Bei … anmelden“** (Accounts → Log in to …). Your
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
`moderator:manage:banned_users` (timeout/ban).

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

Google needs three parts: a **project** with the YouTube API enabled, the **consent screen**
(called "Google Auth Platform" in today's console) and the **OAuth client**.
The console may be shown in your language; the sections are named equivalently.

### 1. Project and API

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a **new project** via the project picker at the top (name e.g. `multichat`).
2. Open the [API library → YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) and click **Enable**.
   Billing is not required for this.

### 2. Consent screen (Google Auth Platform)

Menu **☰ → Google Auth Platform** (or "APIs & Services → OAuth consent screen") → **Get started**:

| Step | Value |
|---|---|
| **App information** | app name e.g. `MultiChat`, support email: your address |
| **Audience** | **External** (with a personal Google account this is the only option) |
| **Contact information** | your email address |
| **Finish** | accept the "Google API Services: User Data Policy" → **Create** |

Then:

- **Audience → Test users → Add users:** add the Google account you'll use to log in to YouTube in MultiChat (the account that owns the YouTube channel or is a moderator there). **Without this entry Google refuses the login.**
- **Data Access → Add or remove scopes:** add `https://www.googleapis.com/auth/youtube.force-ssl` and save. (Recommended – this way the console shows which permission the app uses.)

### 3. OAuth client

**Google Auth Platform → Clients → Create client**:

| Field | Value |
|---|---|
| **Application type** | **Web application** |
| **Name** | anything, e.g. `MultiChat` |
| **Authorized JavaScript origins** | leave empty |
| **Authorized redirect URIs** | **Add URI** → `http://localhost:17563/callback` |

Click **Create**. Now copy the **client ID** and **client secret** – Google shows the full secret
only at this moment (you can create a new one later).

### Good to know

- **Login expires after 7 days:** while the app's publishing status is **Testing**, Google only
  issues logins that expire after 7 days. Just log in again in MultiChat then.
  If you don't want that, switch to **Publish app / In production** under **Audience**.
  Because `youtube.force-ssl` is a sensitive scope, Google then shows "Google hasn't verified this
  app" at login – for your own use, confirm via **Advanced → Go to …**.
  A verification by Google isn't needed for personal use.
- **Quota:** sending and moderating via the YouTube API consumes quota
  (default: 10,000 units per day and project). That's enough for normal moderation; reading the
  chat and the video don't use the API and cost nothing.

📖 Docs: [YouTube Data API – OAuth 2.0 for web server apps](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps) ·
[Configure the consent screen](https://developers.google.com/workspace/admin/reports/v1/guides/authorizing) ·
[Testing vs. production, test users](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)

---

## Login troubleshooting

| Message / behaviour | Cause |
|---|---|
| Browser shows "redirect_uri mismatch" / "invalid redirect" | the redirect URL in the developer app differs – enter exactly `http://localhost:17563/callback` |
| „Port 17563 belegt“ (port 17563 in use) | another login is still running or another program uses the port – wait a moment or restart MultiChat |
| Google: "Access blocked" / "access_denied" | the Google account is missing under **Audience → Test users** |
| Twitch: login fails at the end (e.g. "invalid client") | Client Type is *Public* instead of *Confidential* – public apps have no secret; create a new app with *Confidential* |
| Sending works, moderating doesn't | you're not a moderator on the channel |
| Kick: login or an action is rejected | a required scope isn't ticked in the Kick app |
| „Sitzung abgelaufen – bitte neu anmelden“ (session expired – please log in again) | token expired (with Google in Testing status after 7 days) – log in again |

The log always has more details: **„Hilfe → Log kopieren“** (Help → Copy log).
