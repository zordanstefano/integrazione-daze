# Unofficial Daze Integration for Home Assistant

Custom integration per le wallbox **Daze** (Dazebox Home / Home T) che comunica con i servizi
cloud di Daze (`webapi.dazeservice.com`), gli stessi usati dall'app Daze.

## Stato del progetto (Beta)

Questa è la prima versione pubblica dell'integrazione. È stata sviluppata e testata su hardware
reale, ma il tempo di utilizzo è ancora limitato. Al momento è stata verificata esclusivamente
con una Daze Home T e la compatibilità con altri modelli Daze non è ancora stata confermata.

Il progetto è quindi da considerarsi **Beta**. Sono benvenute segnalazioni di bug, suggerimenti,
test su altri modelli e contributi della community.

## Limite di ricarica: solo gli step della scala Daze

L'app ufficiale espone una **scala discreta** di potenze (1,5 → 7,3 kW a passi di 0,2 kW, più
7,4 kW; 31 valori in tutto su impianto monofase) e mostra la potenza solo quando la corrente
impostata coincide *esattamente* con uno di questi step: fuori scala, app e wallbox restano
disallineate.

L'integrazione aggancia quindi **ogni scrittura allo step più vicino**, da qualunque origine
arrivi (slider, tendina, servizio, automazione), e riconduce allo step anche il valore riletto.
`number.*_potenza_massima_di_carica` espone l'attributo `valori_ammessi` con l'elenco degli
step, così le automazioni possono scegliere direttamente un valore valido.

Il limite **si imposta solo in potenza**: la corrente è esposta come sensore in sola lettura
(`sensor.*_corrente_massima`), perché in ampere è facile scegliere un valore fuori scala.
Per impostarlo ci sono due strade:

- **`select.*_potenza_massima_di_carica`** — tendina con i 31 valori esatti (`1,5 kW` … `7,4 kW`).
  È il modo senza ambiguità: si sceglie il valore, non un'approssimazione.
- **`number.*_potenza_massima_di_carica`** — cursore, comodo ma con un limite del frontend HA:
  le posizioni del cursore sono multipli di `step` calcolati **da zero**, non da `min`, mentre
  la scala Daze è sfalsata di 0,1. Il passo è quindi 0,1 kW (metà scala) perché tutti i valori
  siano raggiungibili; metà delle posizioni continua ad agganciarsi al vicino.

La scala vive in `const.py` (`CHARGING_POWER_STEPS`).

## Cosa fa

| Entità | Tipo | Note |
|---|---|---|
| `sensor.*_potenza` | Potenza istantanea (kW) | 3 decimali |
| `sensor.*_energia_sessione` | Energia erogata nella sessione (kWh) | 3 decimali |
| `sensor.*_energia_totale` | Energia totale erogata (kWh) | cumulativa e monotòna, adatta all'Energy Dashboard |
| `sensor.*_corrente_l1/l2/l3` | Corrente per fase (A) | 2 decimali; l'API la dà in mA. L2/L3 disabilitate di default |
| `sensor.*_tensione_l1/l2/l3` | Tensione per fase (V) | L2/L3 disabilitate di default |
| `sensor.*_durata_sessione` | Durata della ricarica | testo `1 h 23 min`, secondi ignorati |
| `sensor.*_stato` | Stato della wallbox (enum) | |
| `binary_sensor.*_veicolo_collegato` | Veicolo connesso | |
| `binary_sensor.*_in_ricarica` | Ricarica in corso | |
| `binary_sensor.*_anomalia` | Errore di sistema | |
| `sensor.*_corrente_rete_l1/l2/l3` | Corrente prelevata dalla rete (A) | L2/L3 disabilitate di default |
| `switch.*_ricarica` | Avvia/ferma la ricarica | riprende se in pausa |
| `sensor.*_corrente_massima` | Corrente massima impostata (A) | **sola lettura**: si imposta in potenza |
| `number.*_potenza_massima_di_carica` | Potenza massima (kW) | cursore, solo step validi |
| `select.*_potenza_massima_di_carica` | Potenza massima (kW) | stessa impostazione e stesso nome del `number`, scelta esatta in tendina |
| `switch.*_avvio_automatico` | Autostart alla connessione | |

## Icona dell'integrazione

`brand/icon.png` è un'icona **originale** (una wallbox stilizzata con un fulmine), non il logo
Daze: questo progetto non è affiliato a Daze e non ne ridistribuisce il marchio. Da **HA 2026.3**
le custom integration possono fornire i propri brand localmente in una cartella `brand/` — prima
l'unica via era una PR al repository
[home-assistant/brands](https://github.com/home-assistant/brands), e senza quella la UI mostrava
"Icon not available". Nessuna modifica al manifest, i file locali hanno la precedenza sul CDN.
Nomi accettati: `icon.png`, `logo.png`, più le varianti `dark_*` e `*@2x`.

L'icona è fornita in `icon.png` (256×256) e `icon@2x.png` (512×512).

## Requisiti

- Home Assistant 2024.12 o successivo
- Un account Daze (le stesse credenziali dell'app)
- Connessione a internet (l'integrazione parla col cloud Daze, non con la wallbox in LAN)

## Installazione

**Manuale:** copia `custom_components/daze_cloud` in `<config>/custom_components/` e riavvia HA.

**HACS:** aggiungi questo repository come *custom repository* di tipo *Integration*.

Poi: *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Daze (Unofficial)*.
Inserisci email e password Daze; se l'account ha più wallbox potrai sceglierla.

## Opzioni

Dalla scheda dell'integrazione, *Configura*:

- **Intervallo di aggiornamento** (default 30 s, min 10)
- **Corrente minima / massima** (default 6–32 A) — definiscono i limiti dello slider

## Note e limiti

- **Dipende dal cloud Daze.** Se i server Daze o la tua connessione sono giù, l'integrazione
  non funziona: la wallbox si controlla solo tramite i servizi cloud di Daze.
- **Ogni aggiornamento fa 2 chiamate** (`remoteInfo` del connettore + contratto EVSE), perché
  la corrente impostata e le correnti di rete vivono solo sul secondo endpoint.
- **Corrente e potenza sono la stessa impostazione.** La wallbox si comanda solo in corrente
  (è così per tutte le wallbox AC: lo standard IEC 61851 fa comunicare all'auto gli *ampere*
  massimi). L'entità in kW è una comodità: converte con la stessa matematica dell'app ufficiale
  — 230 V nominali per fase e troncamento dei milliampere — quindi i valori coincidono con
  quelli che vedi nell'app. Muovendone una, l'altra si aggiorna al polling successivo.
- **Corrente e tensione di carica a riposo restano vuote** ("Sconosciuto"), non mostrano un testo
  tipo "Non in carica": un sensore che a volte è testo non può avere unità di misura né
  statistiche, e la "V" sulla tensione vale più dell'etichetta esplicita.
- **A riposo** l'API restituisce `chargeSession: null`: potenza, energia e correnti di ricarica
  vengono riportate a `0` invece che "sconosciuto", così i grafici restano continui.
- **Energia totale** (`sensor.*_energia_totale`) è cumulativa e monotòna: viene ricavata
  dall'endpoint di energia della rete Daze, non da un contatore a vita della singola wallbox.
  È adatta come sorgente per l'*Energy Dashboard*.
- Non usare intervalli di polling troppo aggressivi: è un'API cloud di terze parti.

## Verifica senza Home Assistant

```bash
python3 tools/test_daze_api.py --username tua@email.it
```

Fa login, elenca le wallbox e stampa lo stato del connettore. La password viene chiesta a
runtime e non viene mai stampata. Con `--test-command` puoi provare anche start/stop/corrente
(chiede sempre conferma prima di inviare).

## Come è stata realizzata

L'integrazione comunica con gli stessi servizi cloud usati dall'app Daze. L'API non è
documentata pubblicamente: l'integrazione ne implementa un client compatibile.

## Avvertenze

Questa integrazione è distribuita gratuitamente, senza alcuna garanzia. Prima di utilizzarla,
assicurati di comprenderne il funzionamento e verifica che la configurazione sia adatta al tuo
impianto. L'autore non è affiliato a Daze e non fornisce alcuna garanzia sul funzionamento
continuo o sulla compatibilità futura con i servizi Daze.

L'uso è a tuo esclusivo rischio: l'autore non è responsabile per eventuali danni a dispositivi,
impianti, veicoli o cose derivanti dall'uso dell'integrazione.

«Daze» e «Dazebox» sono marchi dei rispettivi titolari. Questo progetto non è affiliato,
associato, autorizzato né approvato da Daze.
