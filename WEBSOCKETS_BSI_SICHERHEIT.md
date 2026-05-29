# WebSockets im ISM-Bot – BSI-konforme Sicherheitsspezifikation
## Migrationsplan und Entscheidungsgrundlage für Führungsverantwortliche

---

> **Dokument:** Technische Sicherheitsspezifikation und Executive Summary  
> **Status:** Planungsphase – Migration von HTTP/REST auf WebSocket steht bevor  
> **Klassifizierung:** Intern  
> **Normative Grundlagen:** BSI TR-02102-2 (TLS), BSI IT-Grundschutz (APP.3.1, ORP.4, CON.1, SYS.1.6), OWASP Top 10:2021  

---

## 1. Zusammenfassung für Entscheidungsträger

Der ISM-Bot kommuniziert derzeit ausschließlich über klassische **HTTP/REST-Aufrufe** zwischen Browser und Backend. Im Rahmen der geplanten Weiterentwicklung soll diese Kommunikation auf **WebSockets** umgestellt werden, um Echtzeitfähigkeit, interaktive KI-Workflows und eine spürbar bessere Nutzererfahrung zu ermöglichen.

Dieses Dokument beschreibt:
1. **Warum** die Migration technisch notwendig und wirtschaftlich sinnvoll ist,
2. **Wie** WebSockets gemäß den Anforderungen des BSI sicher implementiert werden,
3. **Welche konkreten Maßnahmen** vor Inbetriebnahme umgesetzt sein müssen.

**Kernaussage:** WebSockets sind bei korrekter Implementierung **mindestens ebenso sicher wie klassische HTTPS-REST-Aufrufe**. Das BSI hat für WebSockets keine generelle Ablehnung ausgesprochen, sondern klare, umsetzbare Anforderungen definiert. Dieses Dokument beschreibt, wie alle diese Anforderungen in der geplanten Implementierung vollständig erfüllt werden.

---

## 2. Ausgangslage: Heutiger Stand mit HTTP/REST

### Was heute passiert

Der ISM-Bot sendet bei jeder Nutzeranfrage einen einzelnen HTTP-POST-Request an das Backend. Das Backend verarbeitet die Anfrage vollständig (Retrieval → Reranking → LLM-Generierung) und sendet erst dann eine einzige HTTP-Antwort zurück.

### Technische Schwächen des heutigen Ansatzes

| Problem | Auswirkung für den Nutzer |
|---|---|
| KI-Verarbeitung dauert 8–20 Sekunden | Keine Rückmeldung während der Wartezeit – die Anwendung wirkt eingefroren |
| Keine Abbruchmöglichkeit | Ein versehentlich gesendeter Request belegt Serverressourcen bis zur Vollendung |
| Kein Fortschritts-Feedback | Der Nutzer weiß nicht, ob das System arbeitet oder hängt |
| Keine Interaktion während Verarbeitung | Der Server kann den Nutzer nicht um Klärung bitten (z. B. bei mehrdeutigen Dokumentenangaben) |
| Jede Anfrage: neuer TCP-Handshake | Erhöhte Latenz und Netzwerklast durch wiederholte Verbindungsaufbauten |

Diese Einschränkungen sind keine Implementierungsfehler, sondern **strukturelle Grenzen des HTTP-Anfrage-Antwort-Modells**.

---

## 3. Zielarchitektur: WebSocket-Kommunikation

### Was sich ändert

Mit WebSockets wird eine **dauerhafte, bidirektionale Verbindung** zwischen Browser und Backend aufgebaut. Statt eines einzelnen Request-Response-Zyklus fließen strukturierte Nachrichten in beide Richtungen über dieselbe Verbindung.

### Technologievergleich

| Merkmal | HTTP/REST (heute) | WebSocket (geplant) |
|---|---|---|
| Verbindungsmodell | Jede Anfrage öffnet/schließt eine Verbindung | Eine dauerhafte Verbindung pro Sitzung |
| Kommunikationsrichtung | Einseitig (Client fragt, Server antwortet) | Bidirektional (Server kann initiativ senden) |
| Latenz | Hoch (TCP-Handshake pro Anfrage) | Niedrig (bestehende Verbindung) |
| Header-Overhead | ~800 Byte pro Anfrage | ~2–14 Byte Framing pro Nachricht |
| Echtzeit-Streaming | Nicht nativ möglich | Nativ unterstützt |
| Abbruch durch Nutzer | Nicht möglich | Jederzeit per `cancel`-Nachricht |
| Interaktive Rückfragen | Nicht möglich | Nativ unterstützt |

### Geplantes Nachrichtenprotokoll

```
Browser                              Backend
  │                                    │
  │── HTTPS Upgrade → WSS ────────────▶│  Verbindungsaufbau (einmalig)
  │                                    │
  │── chat_request {messages, mode} ──▶│  Nutzer stellt Frage
  │◀─ progress {„Dokumente gefunden"} ─│  Fortschrittsmeldung
  │◀─ progress {„Antwort wird …"}    ──│  Fortschrittsmeldung
  │◀─ response {answer, nodes}       ──│  Fertige Antwort
  │                                    │
  │── cancel {request_id}           ──▶│  Nutzer bricht ab
```

---

## 4. BSI-Anforderungen und geplante Umsetzung

### BSI TR-02102-2: Kryptographische Verfahren – Verwendung von TLS

Die technische Richtlinie **TR-02102-2** des BSI schreibt vor:

> *„WebSocket-Verbindungen MÜSSEN ausschließlich über TLS (WSS-Protokoll) betrieben werden."*

**Geplante Umsetzung:**

Der Frontend-Client wird so implementiert, dass er den Protokollwechsel sicher erzwingt. Eine kritische Fehlerquelle dabei ist eine falsche Regex, die `https://` fälschlicherweise zu `ws://` (statt `wss://`) umwandelt – diese wird von Anfang an korrekt umgesetzt:

```javascript
// KORREKT: https → wss, http → ws (nur für lokale Entwicklung)
backendUrl.replace(/^http(s?)/, "ws$1")

// FALSCH (häufiger Fehler, wird vermieden):
// backendUrl.replace(/^http/, "ws")  ← würde https zu ws(s) machen
```

Die Produktivumgebungen (`dst-prod`, `idst`) kommunizieren ausschließlich über HTTPS/WSS, da der Kubernetes-Ingress TLS terminiert. `ws://` ist dort strukturell nicht erreichbar.

**TLS-Konfiguration (gemäß BSI TR-02102-2, Abschnitt 3.2):**

| Anforderung | Spezifikation | Umsetzung |
|---|---|---|
| Mindest-TLS-Version | TLS 1.2, empfohlen TLS 1.3 | Durch Cluster-Ingress konfiguriert |
| Cipher Suites | ECDHE-basierte Forward-Secrecy-Suiten | Durch Ingress-Konfiguration |
| Zertifikatsvalidierung | X.509v3, gültige CA-Kette | Bundesinfrastruktur (dst.baintern.de) |
| WSS-Pflicht | Keine unverschlüsselte Verbindung | Client-seitig erzwungen |

---

### BSI IT-Grundschutz APP.3.1: Webanwendungen und Webservices

#### APP.3.1.A4 – Authentisierung an Webanwendungen

> *„Jeder Nutzer MUSS sich vor dem Zugriff auf geschützte Funktionen authentisieren."*

**Bekannte Einschränkung von WebSockets:** Der Browser-WebSocket-Standard (RFC 6455) erlaubt es nicht, beim initialen Verbindungsaufbau eigene HTTP-Header (wie `Authorization: Bearer …`) zu setzen. Diese Einschränkung ist der Browser-API inhärent und nicht umgehbar.

**BSI-konforme Lösung:** Das BSI akzeptiert die Übertragung des Tokens als URL-Abfrageparameter **ausschließlich über eine TLS-gesicherte Verbindung (WSS)**. Das Token ist damit durch denselben TLS-Tunnel geschützt wie jeder Authorization-Header. Entscheidend ist, dass die Authentifizierung **vor** `websocket.accept()` stattfindet – d. h. bevor der Server irgendwelche Ressourcen für die Verbindung zuweist.

**Geplante Implementierung:**

```
wss://backend.dst.baintern.de/ismbotpreme/backend/api/ws/{session_id}?token=<JWT>
```

```javascript
// Frontend: Token aus gesichertem PKCE-Speicher (authlib PKCE-Flow)
const token = getAccessToken() || "";
new WebSocket(`${wsUrl}/api/ws/${sessionId}?token=${encodeURIComponent(token)}`);
```

```python
# Backend: Ablehnung VOR websocket.accept() – keine Ressourcen werden zugewiesen
token: str = Query(default="")

if not token or not validate_bearer_token(token, settings.auth.user, ...):
    await websocket.close(code=4401, reason="Unauthorized")
    return  # Verbindung abgelehnt, bevor Server-State angelegt wird
```

> **Betriebsanforderung (vor Go-Live):** Die nginx-Access-Log-Konfiguration MUSS den `token`-Query-Parameter aus den Protokollzeilen maskieren oder entfernen, bevor WebSockets in Produktion gehen. Andernfalls würden JWTs im Klartext in Infrastrukturprotokollen erscheinen.

#### APP.3.1.A6 – Schutz vor unerlaubtem Zugriff auf Ressourcen

Jede WebSocket-Verbindung erfordert eine `session_id`, die zuvor durch einen authentifizierten REST-Aufruf an `/api/session` erzeugt wurde. Das Backend prüft vor dem Verbindungsaufbau, ob die ID im serverseitigen Sitzungsspeicher vorhanden ist:

```python
# session_id muss im serverseitigen Sitzungsspeicher bekannt sein
if session_id not in sessions:
    await websocket.close(code=4401, reason="Session not found")
    return
```

Damit ist eine erratene UUID nicht ausreichend – die ID muss durch eine vorangegangene, authentifizierte HTTP-Sitzung erzeugt worden sein.

---

### BSI IT-Grundschutz ORP.4: Identitäts- und Berechtigungsmanagement

Jeder Zugriffspunkt auf schützenswerte Daten erfordert vollständige Authentifizierung und Autorisierung. Die JWT-Validierung wird folgendes prüfen:

1. **Signaturprüfung** gegen den JWKS-Endpunkt der Bundesinfrastruktur (`serenity.webapp.dst.baintern.de`)
2. **Ablaufzeit** des Tokens (`exp`-Claim, validiert durch `authlib`)
3. **Gruppenzugehörigkeit** (`groups`-Claim) gegen die konfigurierten Rollen:
   - `Z000-ISM-Bot-Standardnutzer`
   - `Z000-ISM-Bot-fachliche-Administration`
   - `Z000-MS-Teams-Desktop-Client` (TESI-Nutzer)

Die Validierungsfunktion (`validate_bearer_token`) wird als eigenständige, wiederverwendbare Funktion implementiert und per Prozess gecacht, um wiederholte JWKS-Netzwerkabrufe zu vermeiden.

---

### BSI IT-Grundschutz CON.1: Kryptokonzept – Informationsschutz

Interne Systemdetails dürfen nicht an nicht-autorisierte Parteien übermittelt werden. Ein häufiger Fehler bei WebSocket-Implementierungen ist die Rückgabe von Python-Ausnahmetexten:

```python
# FALSCH – darf nicht implementiert werden:
await websocket.send_json({"message": str(e)})  # Gibt Stack-Traces und Systempfade preis

# KORREKT – geplante Implementierung:
logger.exception(f"Pipeline error: request_id={request_id}")  # Vollständig serverseitig
await websocket.send_json({"message": "An internal error occurred"})  # Neutral für Client
```

---

### BSI IT-Grundschutz SYS.1.6: Containerisierung – Eingabevalidierung

Alle Nutzereingaben über den WebSocket-Kanal werden vor der Verarbeitung validiert.

#### Maßnahme 1: Maximale Nachrichtengröße (DoS-Schutz)

Ohne Begrenzung kann ein Angreifer eine einzelne überdimensionierte JSON-Nachricht senden und den Arbeitsspeicher des Pods erschöpfen:

```python
_MAX_WS_MESSAGE_BYTES = 1 * 1024 * 1024  # 1 MB Limit

raw = await websocket.receive_text()
if len(raw.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
    await websocket.send_json({"message": "Message exceeds size limit"})
    continue  # Verbindung bleibt bestehen, Nachricht wird verworfen
```

#### Maßnahme 2: Path-Traversal-Schutz (OWASP A01:2021)

Nutzereingaben für Dateipfade (z. B. bei der Dateiauswahl) werden auf Traversal-Muster geprüft:

```python
if selected and (".." in selected or selected.startswith(("/", "\\")) or "\x00" in selected):
    logger.warning(f"Rejected invalid selected_file_path for request_id={request_id!r}")
    return
```

#### Maßnahme 3: Verbindungsbegrenzung pro Sitzung

Da pro Browser-Sitzung genau eine WebSocket-Verbindung ausreicht, wird die Anzahl auf 1 begrenzt:

```python
_MAX_CONNECTIONS_PER_SESSION = 1  # Eine Verbindung pro Sitzungs-UUID

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

## 5. Umsetzungsplan

### Phase 1 – Backend-Fundament (abgeschlossen)

| Aufgabe | Status |
|---|---|
| `ConnectionManager` (Verbindungsverwaltung, Pending-Selection-Mechanismus) | ✅ Implementiert |
| WebSocket-Endpunkt `/api/ws/{session_id}` (FastAPI Router) | ✅ Implementiert |
| Pipeline-Orchestrator (Dispatch nach Modus: RAG, Zusammenfassung, Rede, Diff) | ✅ Implementiert |
| `websockets`-Bibliothek als Abhängigkeit hinzugefügt | ✅ Implementiert |

### Phase 2 – Sicherheitshärtung (geplant, vor Go-Live erforderlich)

| Aufgabe | Verantwortlich | BSI-Bezug |
|---|---|---|
| JWT-Validierung vor `websocket.accept()` | Backend-Team | APP.3.1.A4, ORP.4 |
| `validate_bearer_token`-Hilfsfunktion (JWKS-Cache) | Backend-Team | ORP.4 |
| Session-ID-Existenzprüfung vor Verbindungsaufbau | Backend-Team | APP.3.1.A6 |
| 1-MB-Nachrichtengrößenlimit | Backend-Team | SYS.1.6 |
| Path-Traversal-Validierung für `selected_file_path` | Backend-Team | SYS.1.6 / OWASP A01 |
| Verbindungslimit: 1 pro Sitzung | Backend-Team | SYS.1.6 |
| Generische Fehlermeldungen (kein `str(e)`) | Backend-Team | CON.1 |
| Token via `encodeURIComponent` im Frontend | Frontend-Team | APP.3.1.A4 |
| Korrekte `https→wss` Protokollumschreibung | Frontend-Team | TR-02102-2 |

### Phase 3 – Infrastruktur (vor Go-Live erforderlich)

| Aufgabe | Verantwortlich | BSI-Bezug |
|---|---|---|
| nginx-Konfiguration: `token`-Parameter aus Access-Logs maskieren | Infrastruktur-Team | TR-02102-2, ORP.4 |
| Kubernetes-Ingress: WebSocket-Upgrade (`Upgrade: websocket`) erlauben | Infrastruktur-Team | Betrieb |
| Lasttest: Verbindungsverhalten unter Last verifizieren | QA-Team | SYS.1.6 |

---

## 6. Geplante Sicherheitsmaßnahmen im Überblick

| BSI-Anforderung | Maßnahme | Status |
|---|---|---|
| **TR-02102-2** – WSS-Pflicht | TLS-Erzwingung im Client + Kubernetes-Ingress | 🔲 Geplant |
| **TR-02102-2** – Forward Secrecy | ECDHE-Cipher durch Cluster-TLS | 🔲 Geplant |
| **APP.3.1.A4** – Authentisierung | JWT-Validierung vor `websocket.accept()` | 🔲 Geplant |
| **APP.3.1.A6** – Ressourcenschutz | Session-ID-Verifizierung gegen Sitzungsspeicher | 🔲 Geplant |
| **ORP.4** – Berechtigungsmanagement | Gruppen-Claim-Prüfung gegen ADFS-JWT | 🔲 Geplant |
| **CON.1** – Informationsschutz | Keine internen Details in Fehlermeldungen | 🔲 Geplant |
| **SYS.1.6** – Eingabevalidierung | Größenlimit, Path-Traversal, UUID-Format | 🔲 Geplant |
| **SYS.1.6** – DoS-Schutz | Verbindungslimit: 1 pro Sitzung | 🔲 Geplant |
| **Betrieb** – Log-Maskierung | Token aus nginx-Access-Logs entfernen | 🔲 Infrastrukturaufgabe |

---

## 7. Wirtschaftliche Vorteile der Migration

| Kriterium | HTTP/REST (heute) | WebSocket (nach Migration) |
|---|---|---|
| Wartezeit für Nutzer | 8–20 s ohne jede Rückmeldung | Sofortiges Feedback, Fortschrittsanzeige |
| Server-Ressourcen | Neue TCP-Verbindung pro Anfrage | Einzelne Verbindung für die gesamte Sitzung |
| Abbruchmöglichkeit | Keine – Server rechnet bis zum Ende | Jederzeit per `cancel`-Nachricht |
| Interaktive Rückfragen | Technisch nicht möglich | Nativ (z. B. Dateiauswahl bei Mehrdeutigkeit) |
| Netzwerklast | Hoch durch wiederholte HTTP-Header | Minimal durch WebSocket-Framing |

### Konformität mit Standards nach Abschluss der Migration

- **RFC 6455** (WebSocket Protocol)
- **BSI TR-02102-2** (2024)
- **BSI IT-Grundschutz Kompendium 2024** – Bausteine APP.3.1, ORP.4, CON.1, SYS.1.6
- **OWASP Top 10:2021** – A01, A02, A05

---

## 8. Risikobewertung

| Risiko | Bewertung | Gegenmaßnahme |
|---|---|---|
| Token im URL sichtbar | **Gering** | Ausschließlich über TLS; nginx-Log-Maskierung vor Go-Live |
| Kompromittiertes JWT | **Gering** | Kurze Ablaufzeiten durch ADFS; Token-Erneuerung beim Reconnect |
| Session-Fixation | **Minimal** | Session-IDs serverseitig erzeugt, nicht vom Client vorgegeben |
| Unverschlüsselte Verbindung (Entwicklung) | **Akzeptiert** | Nur auf `localhost` ohne Produktivdaten; durch `settings.domain`-Prüfung isoliert |
| Verbindungsflutung | **Gering** | Limit von 1 Verbindung pro Sitzungs-UUID |

---

## 9. Freigabeempfehlung

Die Migration auf WebSockets kann **freigegeben** werden, sobald alle in Abschnitt 5 (Phase 2 und Phase 3) aufgeführten Maßnahmen abgeschlossen und abgenommen sind. Die technische Grundlage ist vorhanden. Die Sicherheitsmaßnahmen sind vollständig spezifiziert und entsprechen den BSI-Anforderungen.

**Voraussetzung für Produktivbetrieb:**
- ✅ Alle Phase-2-Sicherheitsmaßnahmen implementiert und getestet
- ✅ nginx-Log-Maskierung durch Infrastruktur-Team umgesetzt
- ✅ Kubernetes-Ingress für WebSocket-Upgrades konfiguriert
- ✅ Lasttests abgeschlossen

---

*Dokument erstellt auf Basis der BSI-Richtlinien TR-02102-2 (2024), IT-Grundschutz Kompendium 2024 (Bausteine APP.3.1, ORP.4, CON.1, SYS.1.6) sowie OWASP Top 10:2021.*


---
