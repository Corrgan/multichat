# Login einrichten: Entwickler-Apps für Twitch, Kick und YouTube

Zum **Lesen** der Chats braucht MultiChat keinen Login. Zum **Schreiben und Moderieren**
meldest du dich bei der jeweiligen Plattform an. Dafür verlangt jede Plattform eine eigene,
kostenlose *Entwickler-App*, die du einmalig selbst anlegst. Sie liefert dir zwei Werte:

- **Client-ID** – öffentlich, identifiziert deine App
- **Client-Secret** – wie ein Passwort behandeln, niemandem zeigen

Beide trägst du danach in MultiChat unter **Konten → Entwickler-Apps einrichten …** ein und
klickst auf **Konten → Bei … anmelden**. Der Browser öffnet sich, du bestätigst, fertig.

> **Wichtig – bei allen drei Plattformen dieselbe Redirect-URL, exakt so:**
>
> ```
> http://localhost:17563/callback
> ```
>
> `http` (nicht https), `localhost` (nicht 127.0.0.1), Port `17563`, Pfad `/callback`, kein `/` am Ende.
> MultiChat startet beim Login kurz einen kleinen Webserver auf diesem Port, der die Antwort der Plattform entgegennimmt.

Zum Moderieren musst du außerdem auf dem jeweiligen Kanal **Moderator** sein (oder es ist dein eigener Kanal).

---

## Twitch

**Voraussetzung:** Zwei-Faktor-Authentifizierung (2FA) ist für dein Twitch-Konto aktiv
([Einstellungen → Sicherheit und Datenschutz](https://www.twitch.tv/settings/security)).
Nach dem Aktivieren die Entwicklerkonsole einmal neu laden.

1. Öffne die [Twitch-Entwicklerkonsole → Applications](https://dev.twitch.tv/console/apps) und klicke **Register Your Application**.
2. Fülle das Formular so aus:

   | Feld | Wert |
   |---|---|
   | **Name** | frei wählbar, muss auf ganz Twitch einmalig sein, z. B. `MultiChat-deinname`. Am besten ohne das Wort „Twitch“. Der Name erscheint bei der Anmeldung im Zustimmungsfenster. |
   | **OAuth Redirect URLs** | `http://localhost:17563/callback` eintragen und auf **Add** klicken |
   | **Category** | **Chat Bot** (die Kategorie hat keinen Einfluss auf die Funktion) |
   | **Client Type** | **Confidential** – nur dieser Typ bekommt ein Client-Secret, und MultiChat braucht es für den Login (Authorization-Code-Flow). *Public* ist für Apps ohne Secret gedacht und funktioniert hier nicht. |

3. Captcha bestätigen, **Create** klicken.
4. Unter **Developer Applications** bei deiner App auf **Manage** klicken.
5. **Client ID** kopieren. Unter **Client Secret** auf **New Secret** klicken und den Wert kopieren.
   Ein neues Secret macht das alte ungültig – dann in MultiChat auch das neue eintragen.

**Berechtigungen** (Scopes) stellst du bei Twitch nicht in der App ein; MultiChat fragt sie beim Login an:
`user:write:chat` (schreiben), `moderator:manage:chat_messages` (Nachrichten löschen),
`moderator:manage:banned_users` (Timeout/Bann).

📖 Doku: [Twitch – Register Your App](https://dev.twitch.tv/docs/authentication/register-app) ·
[Authentifizierung & Token-Flows](https://dev.twitch.tv/docs/authentication/) ·
[Forum: Confidential oder Public?](https://discuss.dev.twitch.com/t/what-type-of-client-to-choose/63638)

---

## Kick

**Voraussetzung:** Zwei-Faktor-Authentifizierung (2FA) ist für dein Kick-Konto aktiv
(Kontoeinstellungen → Sicherheit). Ohne 2FA erscheint der Entwickler-Bereich nicht.

1. Öffne [kick.com → Einstellungen → Developer](https://kick.com/settings/developer) und klicke **Create new**.
2. Fülle das Formular so aus:

   | Feld | Wert |
   |---|---|
   | **Name** | frei wählbar, z. B. `MultiChat` |
   | **Description** | frei, z. B. `Persönlicher Multi-Chat` |
   | **Redirect URI** | `http://localhost:17563/callback` |
   | **Enable Webhooks** | **aus** – MultiChat braucht keine Webhooks |
   | **Scopes** | genau diese anhaken (siehe unten) |

   | Scope | wofür in MultiChat |
   |---|---|
   | `user:read` | deinen Kick-Namen nach dem Login anzeigen |
   | `channel:read` | die Kanal-ID zum Kanalnamen finden |
   | `chat:write` | Nachrichten senden |
   | `moderation:ban` | Timeout und Bann |
   | `moderation:chat_message:manage` | Nachrichten löschen |

   Die übrigen Scopes (z. B. `streamkey:read`, `channel:write`, `events:subscribe`) **nicht** anhaken – MultiChat braucht sie nicht.
3. **Create App** klicken, danach **Client ID** und **Client Secret** kopieren.

Fehlt ein Scope in der App, schlägt der Login oder später die betreffende Aktion fehl – dann die Häkchen prüfen.

📖 Doku: [Kick – App einrichten](https://github.com/KickEngineering/KickDevDocs/blob/main/getting-started/kick-apps-setup.md) ·
[Kick – Liste aller Scopes](https://github.com/KickEngineering/KickDevDocs/blob/main/scopes/scopes.md)
(Quelltext der offiziellen Kick-Entwicklerdoku)

---

## YouTube (Google Cloud)

Bei Google sind es drei Teile: ein **Projekt** mit freigeschalteter YouTube-API, der
**Zustimmungsbildschirm** (in der Konsole heute „Google Auth Platform“) und der **OAuth-Client**.
Die Konsole kann auf Deutsch übersetzt sein; die Bereiche heißen dann sinngemäß gleich.

### 1. Projekt und API

1. Öffne die [Google Cloud Console](https://console.cloud.google.com/) und lege oben über die Projektauswahl ein **neues Projekt** an (Name z. B. `multichat`).
2. Öffne die [API-Bibliothek → YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) und klicke **Aktivieren / Enable**.
   Eine Abrechnung (Billing) ist dafür nicht nötig.

### 2. Zustimmungsbildschirm (Google Auth Platform)

Menü **☰ → Google Auth Platform** (bzw. „APIs & Dienste → OAuth-Zustimmungsbildschirm“) → **Get started / Jetzt starten**:

| Schritt | Wert |
|---|---|
| **App information** | App-Name z. B. `MultiChat`, Support-E-Mail: deine Adresse |
| **Audience / Zielgruppe** | **External / Extern** (mit einem privaten Google-Konto gibt es nur diese Option) |
| **Contact information** | deine E-Mail-Adresse |
| **Finish** | Richtlinie („Google API Services: User Data Policy“) bestätigen → **Create** |

Danach:

- **Audience → Test users → Add users:** das Google-Konto eintragen, mit dem du dich in MultiChat bei YouTube anmeldest (das Konto, dem der YouTube-Kanal gehört bzw. das dort Moderator ist). **Ohne diesen Eintrag verweigert Google den Login.**
- **Data Access → Add or remove scopes:** `https://www.googleapis.com/auth/youtube.force-ssl` hinzufügen und speichern. (Empfohlen – so siehst du in der Konsole, welche Berechtigung die App nutzt.)

### 3. OAuth-Client

**Google Auth Platform → Clients → Create client**:

| Feld | Wert |
|---|---|
| **Application type** | **Web application / Webanwendung** |
| **Name** | frei, z. B. `MultiChat` |
| **Authorized JavaScript origins** | leer lassen |
| **Authorized redirect URIs** | **Add URI** → `http://localhost:17563/callback` |

**Create** klicken. Jetzt **Client-ID** und **Client-Secret** kopieren – das Secret zeigt Google
nur in diesem Moment vollständig an (danach kannst du ein neues erzeugen).

### Gut zu wissen

- **Login läuft nach 7 Tagen ab:** Solange die App den Status **Testing** hat, gibt Google nur
  Anmeldungen aus, die nach 7 Tagen verfallen. Dann einfach in MultiChat neu anmelden.
  Wer das nicht will, kann unter **Audience** auf **Publish app / In Produktion** umstellen.
  Weil `youtube.force-ssl` eine sensible Berechtigung ist, zeigt Google beim Login dann den Hinweis
  „Google hat diese App nicht überprüft“ – für die eigene Nutzung über **Erweitert → Weiter zu …** bestätigen.
  Eine Prüfung durch Google ist für den Privatgebrauch nicht nötig.
- **Kontingent:** Senden und Moderieren über die YouTube-API verbraucht Kontingent
  (Standard: 10 000 Einheiten pro Tag und Projekt). Für normale Moderation reicht das; das Lesen
  des Chats und das Video laufen ohne die API und kosten nichts.

📖 Doku: [YouTube Data API – OAuth 2.0 für Webserver-Apps](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps) ·
[Zustimmungsbildschirm konfigurieren](https://developers.google.com/workspace/admin/reports/v1/guides/authorizing) ·
[Testing vs. Produktion, Testnutzer](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)

---

## Fehlersuche beim Login

| Meldung / Verhalten | Ursache |
|---|---|
| Browser zeigt „redirect_uri mismatch“ / „invalid redirect“ | Redirect-URL in der Entwickler-App weicht ab – exakt `http://localhost:17563/callback` eintragen |
| „Port 17563 belegt“ | ein anderer Login läuft noch oder ein anderes Programm nutzt den Port – kurz warten bzw. MultiChat neu starten |
| Google: „Zugriff blockiert“ / „access_denied“ | Google-Konto fehlt unter **Audience → Test users** |
| Twitch: Login schlägt beim Abschluss fehl (z. B. „invalid client“) | Client Type ist *Public* statt *Confidential* – Public-Apps haben kein Secret; neue App mit *Confidential* anlegen |
| Senden klappt, Moderieren nicht | du bist auf dem Kanal kein Moderator |
| Kick: Login oder eine Aktion wird abgelehnt | ein benötigter Scope ist in der Kick-App nicht angehakt |
| „Sitzung abgelaufen – bitte neu anmelden“ | Token abgelaufen (bei Google im Testing-Status nach 7 Tagen) – neu anmelden |

Genaueres steht immer im Log: **Hilfe → Log kopieren**.
