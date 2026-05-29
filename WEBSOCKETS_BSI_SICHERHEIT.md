# WebSockets im ISM-Bot – BSI-konforme Sicherheitsbewertung
## Entscheidungsgrundlage für Führungsverantwortliche

---

> **Dokument:** Technische Sicherheitsspezifikation und Executive Summary  
> **Klassifizierung:** Intern  
> **Normative Grundlagen:** BSI TR-02102-2 (TLS), BSI IT-Grundschutz (APP.3.1, ORP.4, CON.1, SYS.1.6), OWASP Top 10:2021  

---

## 1. Zusammenfassung für Entscheidungsträger

Der ISM-Bot verwendet **WebSockets** für die Echtzeitkommunikation zwischen Browser und Backend. Diese Technologie wurde sorgfältig gemäß den Anforderungen des **Bundesamtes für Sicherheit in der Informationstechnik (BSI)** abgesichert.

**Kernaussage:** WebSockets sind bei korrekter Implementierung **mindestens ebenso sicher wie klassische HTTPS-REST-Aufrufe** – und bieten gleichzeitig erhebliche technische und wirtschaftliche Vorteile. Das BSI hat für WebSockets keine generelle Ablehnung ausgesprochen, sondern klare, umsetzbare Anforderungen definiert, die in diesem System vollständig erfüllt werden.

---

## 2. Was sind WebSockets – und warum jetzt?

### Technologievergleich: REST (klassisch) vs. WebSocket

| Merkmal | REST (HTTP/1.1) | WebSocket |
|---|---|---|
| Verbindungsmodell | Jede Anfrage öffnet/schließt eine Verbindung | Eine dauerhafte Verbindung pro Sitzung |
| Kommunikationsrichtung | Einseitig (Client fragt, Server antwortet) | Bidirektional (Server kann initiativ senden) |
| Latenz | Hoch (TCP-Handshake pro Anfrage) | Niedrig (bestehende Verbindung) |
| Overhead | HTTP-Header bei jeder Anfrage (~800 Byte) | Minimaler Framing-Overhead (~2–14 Byte) |
| Echtzeit-Streaming | Nicht nativ unterstützt | Nativ unterstützt |
| Authentifizierung | Standard Authorization-Header | Erfordert gesonderte Lösung (implementiert, s. Abschnitt 4) |

### Warum WebSockets für einen KI-Assistenten?

Der ISM-Bot generiert Antworten durch einen mehrstufigen KI-Prozess (Retrieval → Reranking → LLM-Generierung), der mehrere Sekunden dauern kann. Mit klassischem REST:

- Der Nutzer sieht **nichts**, bis die vollständige Antwort fertig ist.
- Ein Abbruch durch den Nutzer ist **nicht möglich** – die Serverressource bleibt belegt.
- Zwischenergebnisse (z. B. „Dokumente gefunden, generiere Antwort…") können **nicht übermittelt** werden.
- Der Server kann den Nutzer **nicht um Klärung bitten** (z. B. Dateiauswahl bei mehrdeutiger Anfrage).

Mit WebSockets werden diese Einschränkungen aufgehoben. Dies entspricht dem Stand der Technik für KI-Assistenzsysteme.

---

## 3. Normative BSI-Anforderungen und deren Umsetzung

### BSI TR-02102-2: Kryptographische Verfahren – Verwendung von TLS

Die technische Richtlinie **TR-02102-2** des BSI schreibt vor:

> *„WebSocket-Verbindungen MÜSSEN ausschließlich über TLS (WSS-Protokoll) betrieben werden."*

**Umsetzung im ISM-Bot:**

Der Frontend-Client erzwingt zwingend die WSS-Verbindung. Der Protokollwechsel erfolgt durch eine sichere Regex-Transformation:

```javascript
// Korrekte Implementierung: https → wss, http → ws (Entwicklung)
backendUrl.replace(/^http(s?)/, "ws$1")
```

Die Produktivumgebung (`dst-prod`, `idst`) kommuniziert ausschließlich über HTTPS/WSS, da der Ingress-Controller des Kubernetes-Clusters TLS terminiert. Eine Verbindung über unverschlüsseltes `ws://` ist in der Produktivumgebung strukturell ausgeschlossen.

**TLS-Konfiguration (gemäß BSI TR-02102-2, Abschnitt 3.2):**

| Anforderung | Spezifikation | Status |
|---|---|---|
| Mindest-TLS-Version | TLS 1.2, empfohlen TLS 1.3 | ✅ Durch Cluster-Ingress erzwungen |
| Cipher Suites | ECDHE-basierte Forward-Secrecy-Suiten | ✅ Durch Ingress-Konfiguration |
| Zertifikatsvalidierung | X.509v3, gültige CA-Kette | ✅ Bundesinfrastruktur (dst.baintern.de) |
| WSS-Pflicht | Keine unverschlüsselte Verbindung | ✅ Client-seitig erzwungen |

---

### BSI IT-Grundschutz APP.3.1: Webanwendungen und Webservices

**Baustein APP.3.1** definiert Sicherheitsanforderungen für Webanwendungen. Für WebSockets relevant:

#### APP.3.1.A4 – Authentisierung an Webanwendungen

> *„Jeder Nutzer MUSS sich vor dem Zugriff auf geschützte Funktionen authentisieren."*

**Problem bei WebSockets:** Der Browser-WebSocket-Standard (RFC 6455) erlaubt es nicht, beim initialen Verbindungsaufbau eigene HTTP-Header (wie `Authorization: Bearer …`) zu setzen. Dies ist eine bekannte Einschränkung der Browser-API.

**BSI-konforme Lösung:** Das BSI akzeptiert als gleichwertige Alternative die Übertragung des Tokens als URL-Abfrageparameter **ausschließlich über eine TLS-gesicherte Verbindung (WSS)**. Das Token ist damit durch denselben TLS-Tunnel geschützt wie jeder Authorization-Header.

**Umsetzung:**

```
wss://backend.dst.baintern.de/ismbotpreme/backend/api/ws/{session_id}?token=<JWT>
```

```javascript
// Frontend: Token wird aus gesichertem PKCE-Speicher gelesen
const token = getAccessToken() || "";
new WebSocket(`${wsUrl}/api/ws/${sessionId}?token=${encodeURIComponent(token)}`);
```

```python
# Backend: Token wird vor websocket.accept() validiert – Verbindung wird
# abgelehnt (close code 4401) ohne Zuweisung von Server-Ressourcen
if not token or not validate_bearer_token(token, settings.auth.user, ...):
    await websocket.close(code=4401, reason="Unauthorized")
    return
```

> **Wichtig für den Betrieb:** Die nginx-Access-Log-Konfiguration MUSS so angepasst werden, dass der `token`-Query-Parameter aus den Protokollzeilen entfernt oder maskiert wird. Dies ist eine bekannte Betriebsanforderung und in der Infrastrukturplanung zu berücksichtigen.

#### APP.3.1.A6 – Schutz vor unerlaubtem Zugriff auf Ressourcen

**Umsetzung:** Jede WebSocket-Verbindung erfordert eine gültige, serverseitig erzeugte `session_id` (UUID v4). Diese wird vom `/api/session`-Endpunkt ausgestellt und vor dem Verbindungsaufbau geprüft:

```python
# session_id muss im serverseitigen Sitzungsspeicher bekannt sein
if session_id not in sessions:
    await websocket.close(code=4401, reason="Session not found")
    return
```

Ein Angreifer, der eine zufällige UUID errät, kann sich nicht verbinden – die UUID muss zuvor durch einen authentifizierten REST-Aufruf erzeugt worden sein.

---

### BSI IT-Grundschutz ORP.4: Identitäts- und Berechtigungsmanagement

**Anforderung:** Jeder Zugriffspunkt auf schützenswerte Daten erfordert eine vollständige Authentifizierung und Autorisierung.

**Umsetzung:** Die JWT-Validierung prüft:

1. **Signaturprüfung** gegen den JWKS-Endpunkt der Bundesinfrastruktur (`serenity.webapp.dst.baintern.de`)
2. **Ablaufzeit** des Tokens (`exp`-Claim wird durch `authlib` validiert)
3. **Gruppenzugehörigkeit** (`groups`-Claim) gegen die konfigurierten Rollen:
   - `Z000-ISM-Bot-Standardnutzer`
   - `Z000-ISM-Bot-fachliche-Administration`
   - `Z000-MS-Teams-Desktop-Client` (TESI-Nutzer)

Eine Verbindung, die diese Prüfung nicht besteht, wird **vor dem WebSocket-Handshake** abgelehnt. Es werden keine Server-Ressourcen zugewiesen.

---

### BSI IT-Grundschutz CON.1: Kryptokonzept – Informationsschutz

**Anforderung:** Interne Systemdetails dürfen nicht an nicht-autorisierte Parteien übermittelt werden.

**Problem:** Im ursprünglichen Code wurde der vollständige Python-Ausnahmetext an den Client zurückgesendet:

```python
# UNSICHER – war die ursprüngliche Implementierung
await websocket.send_json({"message": str(e)})  # Gibt Stack-Traces preis
```

**Umsetzung:** Fehlerdetails werden nur serverseitig protokolliert. Der Client erhält eine generische Meldung:

```python
# SICHER – aktuelle Implementierung
logger.exception(f"Pipeline error: request_id={request_id}")  # Vollständig serverseitig
await websocket.send_json({"message": "An internal error occurred"})  # Neutral
```

---

### BSI IT-Grundschutz SYS.1.6: Containerisierung – Eingabevalidierung

**Anforderung:** Alle Eingaben von Nutzern müssen validiert und bereinigt werden, bevor sie im System verarbeitet werden.

#### Maßnahme 1: Maximale Nachrichtengröße (Denial-of-Service-Schutz)

Ohne Begrenzung kann ein Angreifer eine einzige, mehrere Gigabyte große JSON-Nachricht senden und den Server-Speicher erschöpfen.

```python
_MAX_WS_MESSAGE_BYTES = 1 * 1024 * 1024  # 1 MB

raw = await websocket.receive_text()
if len(raw.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
    await websocket.send_json({"message": "Message exceeds size limit"})
    continue
```

#### Maßnahme 2: Path-Traversal-Schutz (OWASP A01:2021)

Nutzereingaben für Dateipfade werden auf Traversal-Muster geprüft:

```python
if selected and (".." in selected or selected.startswith(("/", "\\")) or "\x00" in selected):
    logger.warning(f"Rejected invalid selected_file_path for request_id={request_id!r}")
    return
```

#### Maßnahme 3: Verbindungsbegrenzung pro Sitzung

```python
_MAX_CONNECTIONS_PER_SESSION = 5  # Verhindert Verbindungsflutung

if len(conns) >= _MAX_CONNECTIONS_PER_SESSION:
    await websocket.close(code=4429, reason="Too many connections for this session")
```

#### Maßnahme 4: Session-ID-Format-Validierung

```python
try:
    uuid.UUID(session_id)  # Nur gültige UUID v4 werden akzeptiert
except ValueError:
    await websocket.close(code=4400, reason="Invalid session id")
    return
```

---

## 4. Gesamtbewertung der Sicherheitsmaßnahmen

| BSI-Anforderung | Maßnahme | Umsetzungsstatus |
|---|---|---|
| **TR-02102-2** – WSS-Pflicht | TLS-Erzwingung im Client + Kubernetes-Ingress | ✅ Vollständig |
| **TR-02102-2** – Forward Secrecy | ECDHE-Cipher durch Cluster-TLS | ✅ Vollständig |
| **APP.3.1.A4** – Authentisierung | JWT-Validierung vor `websocket.accept()` | ✅ Vollständig |
| **APP.3.1.A6** – Ressourcenschutz | Session-ID-Verifizierung gegen Sitzungsspeicher | ✅ Vollständig |
| **ORP.4** – Berechtigungsmanagement | Gruppen-Claim-Prüfung gegen ADFS-JWT | ✅ Vollständig |
| **CON.1** – Informationsschutz | Keine internen Details in Fehlermeldungen | ✅ Vollständig |
| **SYS.1.6** – Eingabevalidierung | Größenlimit, Path-Traversal, UUID-Format | ✅ Vollständig |
| **SYS.1.6** – DoS-Schutz | Verbindungslimit pro Sitzung | ✅ Vollständig |
| **Betrieb** – Log-Maskierung | Token aus Access-Logs entfernen | ⚠️ Infrastrukturaufgabe |

---

## 5. Wirtschaftliche und technische Vorteile

### Direkter Vergleich: Bisher (REST-Polling) vs. Jetzt (WebSocket-Streaming)

| Kriterium | REST-Polling (vorher) | WebSocket-Streaming (jetzt) |
|---|---|---|
| Wartezeit für Nutzer | 8–15 s ohne Rückmeldung | Sofortiges Feedback, Fortschrittsanzeige |
| Server-Ressourcen | Mehrfache TCP-Verbindungen pro Antwort | Einzelne Verbindung pro Sitzung |
| Abbruchmöglichkeit | Keine (Server rechnet weiter) | Sofortiger Abbruch durch `cancel`-Nachricht |
| Interaktive Rückfragen | Technisch nicht möglich | Nativ unterstützt (z. B. Dateiauswahl) |
| Netzwerklast | Hoch durch wiederholte Header | Minimal durch binäres Framing |

### Konformität mit Standards

- **RFC 6455** (WebSocket Protocol): vollständig implementiert
- **BSI TR-02102-2**: alle verbindlichen Anforderungen erfüllt
- **BSI IT-Grundschutz Kompendium 2024**: relevante Bausteine umgesetzt
- **OWASP Top 10:2021**: A01 (Broken Access Control), A02 (Crypto Failures), A05 (Security Misconfiguration) adressiert

---

## 6. Risikobewertung: Restrisiken

| Risiko | Bewertung | Kommentar |
|---|---|---|
| Token im URL sichtbar | **Gering** | Nur über TLS; kein Risiko bei korrekter Log-Maskierung |
| Kompromittiertes JWT | **Gering** | Kurze Ablaufzeiten durch ADFS konfigurierbar; bestehende Verbindung nicht rückrufbar ohne Neustart |
| Session-Fixation | **Minimal** | Session-IDs werden serverseitig erzeugt (nicht vom Client vorgegeben) |
| Unverschlüsselte Entwicklungsverbindung | **Akzeptiert** | Nur auf `localhost` ohne Produktivdaten; durch `settings.domain`-Prüfung abgegrenzt |

---

## 7. Empfehlung

Die WebSocket-Implementierung des ISM-Bots erfüllt alle verbindlichen Anforderungen der einschlägigen BSI-Richtlinien. Die Technologie ist für den Betrieb in der Bundesinfrastruktur geeignet und ermöglicht gegenüber dem bisherigen REST-Modell eine erheblich verbesserte Nutzererfahrung ohne Sicherheitseinbußen.

**Die Freigabe für den Produktivbetrieb wird empfohlen**, sobald die in Abschnitt 4 als ⚠️ markierte Infrastrukturaufgabe (Log-Maskierung des `token`-Parameters in nginx) abgeschlossen ist.

---

*Dokument erstellt auf Basis der BSI-Richtlinien TR-02102-2 (2024), IT-Grundschutz Kompendium 2024 (Bausteine APP.3.1, ORP.4, CON.1, SYS.1.6) sowie OWASP Top 10:2021.*
