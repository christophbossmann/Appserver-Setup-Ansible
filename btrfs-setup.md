# Btrfs Setup, Snapshots & Rollback — Ubuntu Server

Diese Anleitung beschreibt das komplette btrfs-Setup für einen Ubuntu-Server: sauberes
`@`-Subvolume-Layout, automatische Snapshots per **snapper**, bootbare Snapshot-Einträge im
**GRUB-Menü** (grub-btrfs) und System-Rollback.

**Arbeitsteilung — wichtig:**

| Wer | Was | Wann |
|---|---|---|
| **`./btrfs-bootstrap.sh`** (Controller-Script, außerhalb Ansible) | Einmaliges **Fundament**: `@`-Layout + snapper + grub-btrfs | **Einmal** pro Host, mit Reboot |
| **Ansible-Rolle `btrfs_snapshots`** | Nur **Checkpoint-Snapshots** (`ans_before_hardening`, …) beim Deploy | Bei jedem `server-all.yml`-Lauf |

Der `@`-Umzug braucht einen Reboot und ist eine Einweg-Migration — deshalb gehört er **nicht**
in ein idempotentes Ansible-Deploy, sondern in ein separates Bootstrap-Script (wie
`ansible-bootstrap.sh`). Ansible **erwartet** ein fertiges btrfs-Setup und erstellt dann nur
noch Snapshots.

---

## 📅 1. Partitionierung im Ubuntu-Installer

Damit Snapshots im laufenden Betrieb ohne Dateikonflikte („Text file busy") laufen, sollte der
Swap **nicht** als Datei im btrfs liegen. Am saubersten: eine **eigene Swap-Partition** im
Installer. (Die `hardening`-Rolle kommt zwar auch mit einem btrfs-Swapfile klar — `chattr +C`
+ `dd` statt `fallocate` —, aber eine Swap-Partition ist einfacher und konfliktfrei.)

**Menü „Custom storage layout":**
1. **Alte Partitionen löschen:** Festplatte (z. B. `/dev/vda`) ➔ `Wipe`/`Format`.
2. **EFI-Partition:** `Add GPT Partition` → **Size** `1G`, **Format** `fat32`, **Mount** `/boot/efi`.
3. **Swap-Partition:** `Add GPT Partition` → **Size** `8G` (RAM-abhängig), **Format** `swap`
   *(Mount + Name leer lassen)*.
4. **Root-Partition:** `Add GPT Partition` → **Size** *(leer = Restplatz)*, **Format** `btrfs`,
   **Mount** `/`.

> Der Ubuntu-**Server**-Installer legt btrfs „flach" an: `/` liegt auf dem **Top-Level-Subvolume
> (ID 5)** — und das lässt sich **nicht** snapshotten. Genau das repariert Phase 1 des Bootstrap-
> Scripts (Umzug nach `@`). Nur der Desktop-Installer/autoinstall legt `@` von sich aus an.

Installation durchziehen, dann per SSH einloggen.

---

## 🚀 2. Fundament aufsetzen — `./btrfs-bootstrap.sh`

Das Script läuft vom **Controller** (wie `ansible-bootstrap.sh`) und SSHt in den Server. Es ist
**zwei-phasig** (weil der `@`-Umzug einen Reboot braucht) und **re-runnable** — es erkennt selbst,
in welcher Phase es ist.

```bash
# ssh_user = sudo-fähiger User (root, Installer-User, oder 'ansible'); ssh_key ist OPTIONAL
# (nur zum Login) — ohne Key wird nach dem Passwort gefragt. Das Script legt KEINEN User an.
./btrfs-bootstrap.sh <target_ip> <ssh_user> [ssh_key_path] [root_device]

# Beispiel (frischer Host, Installer-User 'scratchuser' mit Passwort):
sh ./btrfs-bootstrap.sh 203.0.113.10 scratchuser
```

**Ablauf:**
1. **Phase 1** (`/` noch flach): baut das `@`-Layout auf → großer **Reboot-Banner** (kein
   Auto-Reboot). Danach manuell rebooten:
   ```bash
   ssh <user>@<host> sudo reboot
   ```
2. **Phase 2** (`/` bereits auf `@`): **denselben Befehl nochmal** starten → installiert snapper +
   grub-btrfs, erzeugt einen Verify-Snapshot, schlägt einen letzten Reboot vor.
3. **Verifizieren:** rebooten und per **VNC-Konsole** ins GRUB-Menü schauen — es gibt jetzt ein
   Untermenü **„Ubuntu snapshots"**.

---

## 🔧 3. Was das Script macht (die 4 Pflicht-Schritte)

Eine manuelle `@`-Konvertierung besteht aus **4 Schritten**, die der Ubuntu-Installer bei nativem
`@`-Layout sonst automatisch macht. **Alle vier sind Pflicht** — fehlt einer, bootet das System
zwar, aber Snapshots/GRUB funktionieren nicht richtig.

### Phase 1 (aus dem alten, flachen System)
```bash
DEV=/dev/vda3                                   # die btrfs-Root-Partition
sudo mount -o subvolid=5 "$DEV" /mnt            # Top-Level (ID 5) mounten

# (1) / in das Subvolume @ snapshotten
sudo btrfs subvolume snapshot / /mnt/@
#   -> KEIN `mkdir /mnt/@/.snapshots`! snapper legt .snapshots selbst als Subvolume an und
#      scheitert, wenn dort schon ein leeres Verzeichnis liegt.

# (2) fstab + GRUB-cmdline auf subvol=@
sudo sed -i '/[[:space:]]btrfs[[:space:]]/ s#\bdefaults\b#defaults,subvol=@#' /mnt/@/etc/fstab
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="rootflags=subvol=@ /' /etc/default/grub
sudo grub-editenv /boot/grub/grubenv set btrfs_subvol=@
sudo update-grub
sudo cp /etc/default/grub /mnt/@/etc/default/grub

# (3) @ als DEFAULT-Subvolume setzen
sudo btrfs subvolume set-default /mnt/@

sudo umount /mnt
sudo reboot                                     # -> danach läuft / auf @
```

### Phase 2 (nach dem Reboot, jetzt auf `@`)
```bash
# snapper + grub-btrfs installieren
sudo apt-get install -y snapper btrfs-progs inotify-tools grub2-common bzip2 git make
sudo snapper -c root create-config /            # legt zunächst ein NESTED @/.snapshots an
# Timeline = EIN Snapshot pro Tag (keine Stunden-Stufe): 7 täglich, 4 wöchentlich, 6 monatlich
sudo snapper -c root set-config TIMELINE_CREATE=yes TIMELINE_LIMIT_HOURLY=0 \
     TIMELINE_LIMIT_DAILY=30 TIMELINE_LIMIT_WEEKLY=0 TIMELINE_LIMIT_MONTHLY=0 TIMELINE_LIMIT_YEARLY=0 NUMBER_LIMIT=20
# snapper-timeline.timer läuft standardmäßig STÜNDLICH -> per Drop-in auf täglich
sudo mkdir -p /etc/systemd/system/snapper-timeline.timer.d
printf '[Timer]\nOnCalendar=\nOnCalendar=daily\n' | sudo tee /etc/systemd/system/snapper-timeline.timer.d/daily.conf
sudo systemctl daemon-reload

# FLACHES Layout: .snapshots aus @ herauslösen -> eigenes Top-Level-Subvolume @snapshots,
# via fstab auf /.snapshots gemountet. So überlebt die Snapshot-Historie einen Rollback
# (der tauscht nur @), statt bei jedem Rollback mit @ zerstört zu werden.
DEV=/dev/vda3
sudo umount /.snapshots 2>/dev/null || true
sudo btrfs subvolume delete /.snapshots         # das gerade angelegte NESTED entfernen (leer)
sudo mount -o subvolid=5 "$DEV" /mnt
sudo btrfs subvolume create /mnt/@snapshots     # separates Top-Level-Subvolume
sudo umount /mnt
echo "UUID=$(findmnt -no UUID /) /.snapshots btrfs defaults,subvol=@snapshots 0 0" | sudo tee -a /etc/fstab
sudo mkdir -p /.snapshots && sudo mount /.snapshots && sudo chmod 750 /.snapshots

sudo systemctl enable --now snapper-timeline.timer snapper-cleanup.timer

# grub-btrfs (nicht in den Ubuntu-Repos -> aus git-Tag bauen; bzip2 für die Manpage!)
sudo git clone --depth 1 -b 4.13 https://github.com/Antynea/grub-btrfs.git /usr/local/src/grub-btrfs
sudo make -C /usr/local/src/grub-btrfs install
sudo systemctl enable --now grub-btrfsd         # refresht das Menü bei /.snapshots-Änderung

# GRUB-Menü sichtbar machen (headless VPS: über VNC beim Boot wählbar)
# GRUB_TIMEOUT_STYLE=menu, GRUB_TIMEOUT=5

# (4) GRUB neu installieren, damit der Prefix in @ zeigt  <-- der oft vergessene Schritt!
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --recheck
sudo update-grub
sudo /etc/grub.d/41_snapshots-btrfs             # grub-btrfs.cfg gleich erzeugen (s. Troubleshooting)
```

> **Warum Schritt 4?** GRUBs Config-Prefix (im ESP-Stub `/boot/efi/EFI/ubuntu/grub.cfg`) zeigt
> nach der Installation auf `($root)/boot/grub` **ohne** Subvolume → aufgelöst über das Top-Level
> (ID 5). GRUB liest dann **ewig die alte, flache `grub.cfg`** (ohne Snapshot-Menü), obwohl jedes
> `update-grub` nur `@/boot/grub/…` schreibt. `set-default @` allein reicht **nicht** (nicht jeder
> GRUB-Build honoriert das btrfs-Default-Subvolume). `grub-install` (gebootet auf `@`) backt den
> Prefix explizit als **`($root)/@/boot/grub`** → GRUB liest die richtige cfg.

---

## 📸 4. Snapshots im Betrieb

### Automatisch
- **Timeline:** `snapper-timeline.timer` erstellt **einen Snapshot pro Tag** (Timer per Drop-in
  auf `daily`; Retention via `set-config`, oben: 7 täglich / 4 wöchentlich / 6 monatlich).
  `snapper-cleanup.timer` räumt alte auf (1× täglich).
- **apt:** der snapper-apt-Hook macht `pre`/`post`-Snapshots bei jeder Paketaktion.
- **Deploy-Checkpoints (Ansible):** `server-all.yml` zieht über die Rolle `btrfs_snapshots`:
  `ans_before_hardening` → *(hardening)* → `ans_after_hardening` → … → `ans_after_ansible`
  (`ans` = ansible). `btrfs_checkpoint_skip_if_exists=true` → höchstens ein `ans_<label>_*` pro Host.

btrfs-Snapshots sind **copy-on-write** → ein frischer Snapshot kostet ~0 Platz und wächst erst,
wenn sich Dateien ändern.

### Snapshot manuell erstellen
Der eine Befehl (nur im **normalen** System — im read-only gebooteten Snapshot geht es nicht):
```bash
sudo snapper -c root create -d "vor experiment xyz"     # Snapshot mit Beschreibung
sudo snapper -c root list                                # kontrollieren (# | Datum | Cleanup | Beschreibung)
```
Varianten:
```bash
# Snapshot mit Auto-Cleanup (wird vom number-Cleanup irgendwann entfernt)
sudo snapper -c root create -c number -d "temporär"

# pre/post-Paar um eine riskante Aktion (zeigt hinterher das Diff)
N=$(sudo snapper -c root create -t pre -p -d "vor update")
sudo apt full-upgrade
sudo snapper -c root create -t post --pre-number "$N" -d "nach update"
sudo snapper -c root status "$N"..0            # was sich geändert hat

# Löschen (NIE rm -rf!)
sudo snapper -c root delete 42
```
Der Snapshot erscheint automatisch im GRUB-„Ubuntu snapshots"-Menü (grub-btrfsd refresht bei
`/.snapshots`-Änderung) und kostet dank copy-on-write anfangs ~0 Platz.

### Einen Snapshot dauerhaft behalten
Snapshots mit `Cleanup: number` werden irgendwann automatisch entfernt. Um einen (z. B. den
Verify-Snapshot „Stand nach Installation") **für immer** zu behalten, den Cleanup-Algorithmus
entfernen:
```bash
sudo snapper -c root modify -c "" <N>          # <N> = # aus `snapper list`
```

---

## 🛠️ 5. Rollback

> ⚠️ **`snapper rollback` funktioniert auf diesem Ubuntu-`@`-Layout NICHT** und meldet
> „Cannot detect ambit since default subvolume is unknown". Grund: snapper-`rollback` erwartet
> das openSUSE-Layout, wo `/` **selbst** immer ein Snapshot ist. Hier ist `/` das Subvolume `@`
> (kein snapper-Snapshot) und fstab + GRUB nageln `subvol=@` fest → snapper kann das Default-
> Subvolume nicht „umbiegen". Rollback läuft deshalb über das **Ersetzen von `@`** (unten).

### Kurz: welche Methode wann?
| Ziel | Methode | Reboot |
|---|---|---|
| Nur **Datei-Änderungen** seit Snapshot N zurücknehmen (kaputte Config, schlechtes Paket) | `snapper -c root undochange N..0` | nein |
| Snapshot nur **anschauen** | GRUB → „Ubuntu snapshots" (read-only) | ja (rein/raus) |
| **Kompletter** Rollback in den alten Systemstand | `snapshot rollback <N>` (`@`-Tausch) | ja |

### 0) Schnell: Datei-Undo mit `undochange` (kein Reboot)
Für „mach die Änderungen seit Snapshot `<N>` rückgängig" — spielt die Datei-Differenz rückwärts
ein, ohne Subvolume-Tausch, ohne Reboot:
```bash
sudo snapper -c root list
sudo snapper -c root undochange <N>..0     # <N> = # aus der Liste; 0 = aktueller Stand
```
Ideal für kaputte Configs / schlechte Updates. **Kein** vollwertiger Rollback (nur Dateien —
gelöschte Subvolumes o. ä. bleiben) — dafür B) nehmen.

### A) Zum Anschauen: übers GRUB-Menü (VNC-Konsole)
Beim Boot im GRUB-Menü **„Ubuntu snapshots"** → gewünschten Snapshot → bootet **read-only** in
diesen Zustand (jedes `touch`/Schreiben schlägt fehl — Absicht). Nur zum Prüfen. Zurück ins
normale System: `sudo reboot` → im GRUB **„Ubuntu"** wählen.

> ℹ️ Von *innerhalb* eines gebooteten Snapshots nicht mit `snapper` zurückrollen — nutze B (das
> mountet das Top-Level und sieht die echte Historie).

### B) Echter Rollback: `snapshot rollback` (ein Befehl)
Weil alles fest auf `subvol=@` zeigt, wird `@` durch den Snapshot-Inhalt **ersetzt**. Das geht
**direkt aus dem normalen laufenden System** — kein Snapshot-/Rescue-Boot nötig: ein btrfs-Mount
hängt an der Subvolume-**ID**, nicht am Namen, also lässt sich das laufende `@` gefahrlos
**umbenennen** (genau der Trick, den auch Timeshift + snapper-rollback nutzen).

```bash
# auf dem Server (das installierte Tool):  <N> = # aus `snapshot list`
snapshot rollback <N>            # fragt nach, tauscht @, rebootet
# ODER vom Controller aus (per SSH; der Reboot beendet die Verbindung — normal):
ssh ansible@203.0.113.10 'sudo snapshot rollback <N>'
```
Ablauf: aktuelles `@` → `@_rollback_backup_<datum>` umbenennen (Sicherheitsnetz), frisches `@` aus
Snapshot `<N>` erzeugen, Default-Subvolume setzen, **reboot**. Beim **flachen Layout** (siehe §3):
- die snapper-Historie **≤ #N bleibt erhalten** (`@snapshots` liegt außerhalb von `@`),
- Snapshots **> #N werden gelöscht** (die verworfene Zukunft — die brauchst du nach dem Rollback nicht),
- der aktuelle (verworfene) Stand bleibt als `@_rollback_backup_*` erhalten.

Manuell (flaches Layout, falls ohne Script):
```bash
DEV=/dev/vda3
sudo mount -o subvolid=5 "$DEV" /mnt
OLD="/mnt/@_rollback_backup_$(date +%F_%H-%M)"
sudo mv /mnt/@ "$OLD"                                        # laufendes @ umbenennen (mount = by id, safe)
sudo btrfs subvolume snapshot "/mnt/@snapshots/<N>/snapshot" /mnt/@   # @snapshots ist SEPARAT, Pfad unverändert
# neuere Snapshots (> N) wegwerfen (optional):
#   for d in /mnt/@snapshots/*/; do m=$(basename "$d"); [ "$m" -gt <N> ] 2>/dev/null && \
#     sudo btrfs subvolume delete "$d/snapshot" && sudo rm -rf "$d"; done
sudo btrfs subvolume set-default /mnt/@
sudo umount /mnt
sudo reboot
```
`@_rollback_backup_*` später aufräumen: `sudo btrfs subvolume delete "$OLD"`.

---

## 🗂️ 6. Daten aus dem Rollback ausschließen (separate Subvolumes)

**Prinzip:** Ein btrfs-Snapshot ist **nicht rekursiv** — alles, was ein **eigenes Subvolume** ist,
erscheint im Root-Snapshot nur als leerer Mountpoint und liegt damit **außerhalb von `@`**. Beim
Rollback (`@`-Tausch) wird so ein Subvolume **nicht angefasst**. Genau richtig für Daten, die ein
System-Rollback *nicht* zurückdrehen soll: Docker-Volumes, Datenbanken, Medien-Libraries.

Das ist derselbe Trick wie bei `@snapshots` (§3) — nur für Datenordner. Kandidaten in diesem Setup:
Docker-Datenpfad, Bind-Mount-Datenordner (immich-Library, nextcloud-data, postgres-data), evtl.
`/home`, `/var/log`.

### Mit dem `snapshot`-Tool (empfohlen)
Das Tool macht das Auslagern **und** das isolierte Snapshotten/Zurückrollen in je einem Befehl
(operiert über `-c <config>` auf beliebige Volumes):
```bash
# Ordner in ein eigenes Subvolume auslagern (DIENST VORHER STOPPEN!):
snapshot volume-add immich /opt/immich     # legt @immich + @immich_snapshots + snapper-Config 'immich' an

snapshot volumes                           # alle Volumes/Configs + Mountpoints auflisten
snapshot -c immich create "vor experiment" # Daten-Snapshot
snapshot -c immich list
snapshot -c immich rollback 3              # NUR die immich-Daten zurück — OHNE Reboot
```
`volume-add` richtet gleich das **flache Layout** ein (`@immich_snapshots` separat), also überlebt
die Daten-Snapshot-Historie auch einen Daten-Rollback. Der Rollback eines Datenvolumes hängt es
kurz aus, tauscht `@immich` und hängt wieder ein — **kein Reboot** (nur der System-Rollback von
`root`/`@` rebootet). Historie ≤ N bleibt, Snapshots > N werden gelöscht.

Darunter steht dasselbe manuell — falls du es ohne Tool machen willst:

### Einen bestehenden Ordner nachträglich zu einem Subvolume machen (manuell)
Ein Verzeichnis lässt sich nicht in-place „umwandeln" — man legt ein Subvolume an und zieht die Daten
um. Beispiel `/opt/immich` (**Dienst vorher stoppen**, damit sich die Daten nicht ändern):
```bash
DEV=/dev/vda3
# 1) neues Top-Level-Subvolume + Daten reflink-kopieren (CoW: schnell, platzsparend, gleiche FS)
sudo mount -o subvolid=5 "$DEV" /mnt
sudo btrfs subvolume create /mnt/@immich
sudo cp -a --reflink=always /opt/immich/. /mnt/@immich/
sudo umount /mnt
# 2) alten Ordner beiseite, fstab-Eintrag, mounten
sudo mv /opt/immich /opt/immich.old
sudo mkdir /opt/immich
echo "UUID=$(findmnt -no UUID /) /opt/immich btrfs defaults,subvol=@immich 0 0" | sudo tee -a /etc/fstab
sudo mount /opt/immich
# 3) prüfen (Daten da? Dienst startet?), dann alten Ordner löschen
sudo rm -rf /opt/immich.old
```
Danach ist `@immich` ein Sibling von `@` im Top-Level → **jeder Rollback lässt es unberührt**.

### Wichtig
- **Nicht im Root-Snapshot enthalten:** Das Datensubvolume wird nicht mehr mit dem System
  gesnapshottet (meist gewollt) → separat sichern (restic / `btrfs send`), oder eine **eigene
  snapper-Config** dafür anlegen, wenn du es versionieren willst:
  `sudo snapper -c immich create-config /opt/immich`.
- **Datenbanken:** Postgres-/DB-Datenordner gehören ohnehin aus den Root-Snapshots raus (CoW +
  Random-Writes = Fragmentierung). Zusätzlich `nodatacow` setzen — am saubersten beim Anlegen:
  `sudo chattr +C /mnt/@immich` **vor** dem Befüllen (wirkt nur auf danach geschriebene Dateien).
- **Versions-Skew:** Bei App **und** DB in getrennten Subvolumes kann ein System-Rollback ohne
  Daten-Rollback zu „DB-Schema neuer als App" führen. Bei reinen Medien (Fotos) egal; bei
  App+DB-Paaren (Nextcloud/Postgres) im Kopf behalten.
- **Am saubersten von Anfang an:** Solche Datensubvolumes vor dem ersten Deploy anlegen (im
  `btrfs-bootstrap.sh` oder per Ansible), statt nachträglich umzuziehen.

### ⚠️ Rollback auf einen Snapshot VOR einem Subvolume-Split
Die **`/etc/fstab` liegt in `@`** und wird beim Rollback mitzurückgedreht — sie entscheidet, welche
Subvolumes gemountet werden. Rollst du `@` auf einen Snapshot **vor** dem Auslagern von z. B.
`@immich` zurück, dann:
- hat dessen fstab **keinen `@immich`-Eintrag** → `@immich` wird **nicht gemountet**;
- unter `/opt/immich` erscheinen die **alten, damals in `@` eingebackenen Daten** aus dem Snapshot;
- das separate `@immich` wird vom `@`-Rollback **nicht angefasst** — es existiert weiter, ist nur
  **verwaist** (nichts mountet es). **Deine aktuellen Daten sind also nicht weg, nur nicht gemountet.**

> **Verwaist = unsichtbar, belegt aber weiter Speicher.** Ein verwaistes Subvolume taucht im normalen
> Verzeichnisbaum **nicht** auf (der Mountpunkt ist leer) und die Dienste verhalten sich, als wäre es
> weg — aber seine Blöcke sind **weiterhin allokiert**: `df -h /` zeigt den Platz als **belegt**
> (typische Verwirrung: „Platte voll, aber die Ordner sind leer"). Sichtbar nur mit btrfs-Werkzeugen:
> `sudo btrfs subvolume list /` bzw. `sudo mount -o subvolid=5 <dev> /mnt && ls /mnt`. **Freigegeben
> wird der Speicher erst durch ein explizites `sudo btrfs subvolume delete`** — ein `@`-Rollback macht
> das nie (er tauscht nur `@`, Geschwister-Subvolumes sind außerhalb seines Scopes). Der Erstellungs-
> Zeitpunkt ist egal: ein `@`-Snapshot kann Geschwister-Subvolumes weder anlegen noch löschen.

Zurück zum Split-Layout: den fstab-Eintrag wieder hinzufügen und mounten —
```bash
echo "UUID=$(findmnt -no UUID /) /opt/immich btrfs defaults,subvol=@immich 0 0" | sudo tee -a /etc/fstab
sudo mount /opt/immich
```
— oder einfach auf einen **neueren** Snapshot (nach dem Split) rollen. Die Alt-Daten im
zurückgerollten `@` liegen dann verdeckt unter dem Mount und belegen Platz, bis du sie entfernst.

**Merksatz:** Ein System-Rollback dreht auch das **Mount-Layout** auf den Snapshot-Zeitpunkt zurück.
Nach einem Subvolume-Split daher **Pre-Split-Snapshots am besten löschen** (oder meiden), sonst
landest du im alten Mount-Layout mit verwaistem Datensubvolume.

---

## ♻️ 7. Rollback & Restore — vier Szenarien

Vier realistische Wiederherstellungs-Situationen, jede mit dem passenden Werkzeug. Roter Faden:
**Fotos sind teuer** (Datenrate) → wo möglich bleiben sie lokal; nur wenn die Platte weg ist,
werden sie zurückgeladen.

| # | Situation | Werkzeug | immich-Fotos |
|---|---|---|---|
| **1** | Server läuft, **System kaputt** (unbekannt) → System zurück, DB sauber aus Dump | `snapshot rollback` (System) **+** `immich-restore-local.yml -e immich_restore_files=false` | **bleiben lokal** (kein Download) |
| **2** | **Zeitreise** — ganzen Server (oder nur immich) z. B. **2 Tage** zurück | reiner **btrfs-Rollback** aller/der relevanten Configs (inkl. `immich_data`) | **lokal zurückgedreht** (kein Download) |
| **3** | **Komplette Neuinstallation, Platte lebt** — alles frisch außer den Fotos (Default-DR) | `@`→`ans_subvols_ready` **+ Daten-Subvolumes wipen (außer `immich_data`) + Redeploy + DBs aus Dump** | **bleiben lokal** (kein Download) |
| **4** | Server/Platte **neu installiert**, alles (auch Fotos) weg | voller **Ansible/restic-Restore** inkl. Foto-Originale | **werden zurückgeholt** (groß, dauert) |

**Wer ist welches Subvolume?** (`snapshot volumes` zeigt die echten Namen)

| Was | snapper-Config | Mountpoint |
|---|---|---|
| System (OS, Docker, Configs, Caddy, Compose) | `root` | `/` |
| Postgres-**business** = immich-DB **+** nextcloud-DB | `business_db` | `/srv/business/db` |
| **immich-Fotos/Videos** (inkl. `thumbs/`) | **`immich_data`** | **`/srv/business/immich`** |
| Nextcloud-Dateien (html + user files) | `nextcloud_data` | `/srv/business/nextcloud` |
| Authentik (DB + Daten) | `authentik` | `/srv/authentik` |
| n8n / supabase (+ deren DB) | `dev` | `/srv/dev` |

> **Grundprinzip:** `@immich_data` ist ein **eigenes Subvolume** → der `@`-Tausch beim
> System-Rollback fasst es **nie** an. Fotos gehen nur verloren/mit-zurück, wenn du **`immich_data`
> selbst** anfasst (Szenario 2) oder die Platte weg ist (Szenario 4).

---

### 7.1 — Server läuft, System geschädigt → System zurück, DB aus Dump, Fotos behalten

Der Server **läuft** (Platte lebt), das System ist auf unbekannte Weise kaputt. **btrfs** setzt
das System sofort/lokal zurück; die **DB** kommt aus dem **pg_dump** (bei „unbekannter Schädigung"
der definierte, off-site-fähige Stand — ein btrfs-DB-Snapshot könnte den Schaden mit enthalten);
die **Fotos** bleiben unangetastet.

```bash
# 1) System auf einen sauberen Stand zurückrollen (rebootet). Lässt ALLE Daten-Subvolumes
#    unberührt — inkl. Fotos UND der DB (die kommt gleich frisch aus dem Dump):
sudo snapshot -c root list
sudo snapshot rollback <N_root>
#    Kein vertrauenswürdiger System-Snapshot? Dann das System stattdessen per Ansible frisch
#    bereitstellen (server-all.yml) — der Rest bleibt identisch.

# 2) Nach dem Reboot: NUR die immich-DB aus dem Dump wiederherstellen, Fotos NICHT anfassen.
#    files=false => @immich_data (Fotos + thumbs/) bleibt unangetastet, kein Download:
ansible-playbook playbooks/immich-restore-local.yml \
  --limit <host> -e immich_restore_confirm=YES -e immich_restore_files=false
```
Schritt 2 restauriert `backups/<host>/immich/immich-db/db.dump` und leert dabei die stale
`oauthId` → SSO verknüpft sich beim nächsten Login per E-Mail neu (`immich_restore_clear_oauth`,
default an — greift, falls Authentik zwischenzeitlich frisch aufgebaut wurde).

- **Authentik:** i. d. R. **kein** Restore — deklarativ neu ausrollen (`--tags authentik`).
- **Nextcloud:** eigenes Thema (DB **und** user files gehören zusammen) → `nextcloud-restore-local.yml`
  (Dump + Dateien in einem Lauf; NC ist datenmengen-mäßig unkritisch, ruhig **beides aus dem Backup**).
- **Konsistenz:** DB (Dump-Stand) referenziert eine **Teilmenge** der aktuellen Fotos (append-mostly)
  → konsistent; nach dem Dump importierte Fotos bleiben als verwaiste Dateien liegen (bei
  nächtlichem Dump klein). **Thumbnails** bleiben (in `@immich_data`) → **keine** Regenerierung.

---

### 7.2 — Zeitreise: ganzen Server (oder nur immich) um X Tage zurück (rein btrfs, inkl. Fotos)

Rückgängig machen, was in den letzten Tagen passiert ist — **alles lokal**, inkl. Fotos. Hier
rollst du `immich_data` **mit** zurück, DB und Fotos landen auf **demselben** Zeitpunkt → voll
konsistent, keine verwaisten Fotos.

> Snapshot-**Nummern unterscheiden sich pro Config** → in jeder Config den Snapshot von „vor 2
> Tagen" heraussuchen: `snapshot -c <cfg> list` (Spalte Datum).

**Ganzer Server 2 Tage zurück** (Reihenfolge: Daten-Volumes zuerst, `root` zuletzt = rebootet):
```bash
# Dienste stoppen, die auf die Daten-Volumes schreiben (sonst 'mount busy'):
sudo docker stop immich-server immich-machine-learning nextcloud postgres-business \
                 authentik-server authentik-worker authentik-postgres
# Daten-Volumes auf den 2-Tage-Stand (jeweils OHNE Reboot):
sudo snapshot -c business_db     rollback <N_db>
sudo snapshot -c immich_data     rollback <N_immich>   # <-- HIER werden die Fotos mit-zurückgedreht
sudo snapshot -c nextcloud_data  rollback <N_nc>
sudo snapshot -c authentik       rollback <N_ak>       # optional
sudo snapshot -c dev             rollback <N_dev>      # optional
# Zuletzt das System -> rebootet in den 2-Tage-Stand:
sudo snapshot rollback <N_root>
```

**Nur immich 2 Tage zurück** — Achtung, die immich-DB liegt im **gemeinsamen** `business_db`
(zusammen mit der NC-DB), btrfs kann sie **nicht** isoliert zurückrollen. Zwei saubere Wege:
```bash
sudo docker stop immich-server immich-machine-learning
# Fotos + thumbs 2 Tage zurück (kein Reboot):
sudo snapshot -c immich_data rollback <N_immich>
sudo docker start immich-server immich-machine-learning
```
- **Weg a (DB egal / NC darf mit):** zusätzlich `business_db` (und für NC-Konsistenz
  `nextcloud_data`) zurückrollen — dann geht die NC-DB aber **mit** 2 Tage zurück.
- **Weg b (wirklich nur immich):** `immich_data` per btrfs zurück **und** nur die **immich-DB**
  aus einem 2-Tage-alten **Dump** einspielen (restic hält tägliche Historie), ohne die NC-DB
  anzufassen: `immich-restore-local.yml -e immich_restore_files=false` mit dem passenden
  Backup-Snapshot. Rollst du nur die Fotos zurück und lässt die DB aktuell, entstehen **fehlende
  Dateien** für zwischenzeitlich in der DB gelöschte/geänderte Assets → Weg a oder b nehmen.

Jeder Rollback hebt den verworfenen Stand als `@<name>_rollback_backup_<datum>` auf (Sicherheitsnetz;
später `sudo btrfs subvolume delete …`). Snapshots **> N** werden verworfen, Historie **≤ N** bleibt.

> **`-y` für unbeaufsichtigt:** Ohne `-y` fragt jeder `rollback` einzeln nach (`[y/N]`). Für einen
> durchlaufenden Ablauf (z. B. mehrere Daten-Volumes hintereinander) hängst du `-y` an:
> `sudo snapshot -c immich_data rollback <N> -y` bzw. `sudo snapshot rollback <N> -y` (System, rebootet dann sofort).

---

### 7.3 — Komplette Neuinstallation bei lebender Platte — alles außer Fotos zurücksetzen, DBs aus Dump

**Default-DR-Szenario:** Die Platte lebt, aber du willst System **und** alle Datenbanken/Dienste
komplett **frisch** aufsetzen (unbekannte Schädigung, sauberer Neustart) — nur die **immich-Foto-
library** soll wegen ihrer Größe **nicht** über die Leitung. Kombiniert btrfs (System auf bare
zurück) + Wipe der Daten-Subvolumes (außer Fotos) + kompletter Redeploy + DBs aus Dump. Einmal
end-to-end validiert.

```bash
# 1) @ auf den "Subvolumes da, sonst nackt"-Anker zurückrollen (rebootet). Danach sind die
#    Daten-Subvolumes gemountet (Fotos + DBs noch da) — die DBs werden gleich neu/aus Dump:
sudo snapshot -c root list                 # den ans_subvols_ready-Anker suchen
sudo snapshot rollback <N>                 # -> bare System, @immich_data & Co. remounten per fstab

# 2) Nach dem Reboot: alle Daten-Subvolumes LEEREN AUSSER immich_data (und ausser Volumes OHNE
#    Backup!). Loescht nur den Dateninhalt, nicht Subvolume/.snapshots:
sudo rm -rf /srv/business/db/data                     # PGDATA (immich+nextcloud+n8n) -> initdb frisch
sudo rm -rf /srv/business/nextcloud/html /srv/business/nextcloud/data
sudo rm -rf /srv/authentik/db
#   @immich_data (Fotos) NICHT anfassen. @dev (n8n/supabase) hat KEIN Backup -> nur leeren, wenn
#   Datenverlust dort ok ist.

# 3) Kompletter Redeploy -> frische Stacks + leere DBs, am Ende die ans_post_install-Anker:
ANSIBLE_SSH_RETRIES=5 ansible-playbook playbooks/server-all.yml --limit <host>

# 4) DBs aus Dump zurueckspielen (Fotos NICHT anfassen):
ansible-playbook playbooks/immich-restore-local.yml \
  --limit <host> -e immich_restore_confirm=YES -e immich_restore_files=false   # DB aus Dump, Fotos bleiben
ansible-playbook playbooks/nextcloud-restore-local.yml \
  --limit <host> -e nc_restore_confirm=YES                                      # DB + user files
#   Authentik: kein Restore -> der Redeploy hat es deklarativ neu gebaut.
```
- **`authentik-ldap` crash-loopt nach frischem Authentik** (`AUTHENTIK_TOKEN` fehlt — der Outpost-
  Token wird erst nach dem Blueprint-Sync gesetzt): einmal `ansible-playbook … --tags authentik`
  nachziehen → holt den Token, schreibt das env-File, startet den Outpost neu → `Up (healthy)`.
- **Thumbnails:** hier **kein** Regen nötig — `files=false` lässt `@immich_data/thumbs` unberührt
  (anders als Szenario 4, das die thumbless Backup-Library einspielt).
- **Dump-Frische:** die immich-DB landet exakt auf dem Dump-Stand → nimm den **frischesten** Dump
  (nächtlich / vorher `backup-pull`), sonst zeigt immich weniger Assets und neuere Fotos liegen
  verwaist in `@immich_data` (append-only, konsistent, aber nicht sichtbar).
- **Fresh-Baseline:** der Redeploy legt am Ende je Daten-Subvolume einen permanenten
  `ans_post_install`-Anker an (`post_tasks` in `server-all.yml`) — die saubere Rückrollbasis, die
  die Timeline-Snapshots nie liefern.

---

### 7.4 — Restore nach komplettem Neuaufbau (Platte/Server neu, Fotos verloren)

Der Server wurde **komplett neu installiert** → lokale Daten **und** btrfs-Snapshots sind weg,
**inklusive Fotos**. Wiederherstellung läuft komplett aus den **Ansible/restic-Backups**
(off-site). Das ist der langsame Weg — die Foto-Originale kommen über die Leitung. (Genau dieser
Ablauf wurde beim letzten Rebuild gefahren.)

```bash
# 1) Fundament: btrfs-Bootstrap + Basis-Deploy (Subvolumes/Stacks stehen danach):
sh ./btrfs-bootstrap.sh <ip> <user>        # + Reboots (s. §2)
ansible-playbook server-all.yml --limit <host>

# 2) Backups auf den Controller holen (falls nicht schon lokal unter backups/<host>):
#    - lokaler Pull (falls Quelle noch existiert):  playbooks/backup-pull.yml
#    - aus der Cloud (restic):                       backup-restore ...  (siehe roles/backup_restic)

# 3) immich VOLL wiederherstellen INKL. Fotos (files=true ist Default => der große Foto-rsync):
ansible-playbook playbooks/immich-restore-local.yml \
  --limit <host> -e immich_restore_confirm=YES

# 4) Nextcloud wiederherstellen (DB + user files):
ansible-playbook playbooks/nextcloud-restore-local.yml \
  --limit <host> -e nc_restore_confirm=YES
```
- **Authentik:** deklarativ neu (`--tags authentik`) — kein DB-Restore nötig; die immich-`oauthId`
  wird beim Restore geleert → SSO verknüpft per E-Mail neu, Nextcloud per Username.
- **Thumbnails:** der Backup schließt `thumbs/` aus → nach dem Voll-Restore **einmal regenerieren**:
  Administration → **Job Queues** → **Generate Thumbnails** → **All** (sonst „Error loading image").

> ⚠️ btrfs-Snapshots liegen auf **derselben Platte** → bei Plattendefekt weg. Nur die
> **off-site** Ansible/restic-Backups (inkl. Foto-Originale) retten diesen Fall — Szenario 1–3
> setzen eine **lebende Platte** voraus.

---

## 🔍 8. Troubleshooting

**GRUB zeigt kein „Ubuntu snapshots"-Menü**, obwohl snapper + grub-btrfs laufen:
1. **Prefix zeigt nicht auf `@`** (häufigste Ursache — Schritt 4 oben): prüfen mit
   `sudo grep prefix /boot/efi/EFI/ubuntu/grub.cfg` → muss `($root)/@/boot/grub` sein, nicht
   `($root)/boot/grub`. Fix: `grub-install` gebootet auf `@` (s. o.), dann `update-grub`.
2. **`grub-btrfs.cfg` existiert noch nicht:** der Daemon `grub-btrfsd` schreibt sie nur bei einer
   `/.snapshots`-Änderung, **nicht** bei `update-grub` — beim ersten Setup fehlt sie oft beim
   ersten Reboot, der `if [ ! -e grub-btrfs.cfg ]`-Guard blendet das Menü aus. Fix: einmal
   `sudo /etc/grub.d/41_snapshots-btrfs` (erzeugt sie synchron).
3. **Roh-Snapshots ohne snapper-Metadaten** (z. B. altes `btrfs subvolume snapshot` in
   `@snapshots`) → grub-btrfs erzeugt kaputte `grub.cfg` (`out of memory`/`syntax error`,
   `update-grub` failt). Fix: solche Roh-Snapshots löschen, **nur snapper** als Snapshot-System nutzen.

**`snapper create-config` failt „…subvolume .snapshots failed since it already exists"**: ein
leeres `/.snapshots`-Verzeichnis liegt im Weg (z. B. manuell angelegt). Entfernen (`btrfs
subvolume delete /.snapshots` bzw. `rmdir`), dann `create-config` erneut.

**`snapper create` failt „IO Error (.snapshots is not a btrfs subvolume)"**: sollte beim **flachen
Layout** (§3, `@snapshots` separat) nicht mehr auftreten. Falls doch (z. B. `.snapshots` nicht
gemountet): prüfen mit `findmnt /.snapshots` — muss das `@snapshots`-Subvolume zeigen; sonst
`sudo mount /.snapshots` (fstab-Eintrag vorhanden?).

**`snapper create` failt „Creating snapshot failed"**: `/` liegt auf dem **Top-Level (ID 5)** statt
auf `@` → Top-Level lässt sich nicht snapshotten. `findmnt -no OPTIONS / | tr , '\n' | grep subvol`
muss `subvol=/@` zeigen. Fix: die `@`-Konvertierung (Abschnitt 2/3).

**Swap: `swapon failed: Invalid argument`**: btrfs-Swapfile ohne `nodatacow` — `chattr +C` auf die
leere Datei + mit `dd` (nicht `fallocate`) füllen. Besser: eigene Swap-Partition (Abschnitt 1).
