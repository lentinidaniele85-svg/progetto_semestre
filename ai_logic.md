# Architettura Cognitiva e Logica dell'AI

Il progetto "Sustainable Product Optimization" implementa un **Agente Neuro-Simbolico** progettato per assistere ingegneri e designer nell'ottimizzazione sostenibile dei materiali di un prodotto.

## 1. Il Paradigma Neuro-Simbolico
Il sistema non è un semplice chatbot basato solo sull'intelligenza artificiale (LLM), ma divide le responsabilità tra due "motori" complementari:
- **Il Motore Neurale (LLM)**: Gestisce la semantica. Si occupa di comprendere il testo dell'utente, estrarre i vincoli ingegneristici, destrutturare un prodotto complesso in una distinta base (BOM - Bill of Materials) e formulare ipotesi sui materiali.
- **Il Motore Simbolico (Python/Deterministico)**: Gestisce la logica rigida. Si occupa di eseguire le valutazioni matematiche, mappare inflessibilmente la geometria ai processi manifatturieri, interrogare il database LCA (Life Cycle Assessment) e calcolare gli impatti ambientali (es. tonnellate-chilometro per i trasporti) senza possibilità di allucinazione.

> **Regola d'Oro:** L'LLM *non ha mai* l'autorità di inventare numeri riguardanti l'impatto ambientale o le proprietà fisiche. È confinato all'ideazione, mentre Python verifica i dati con il dataset locale.

## 2. L'Orchestrazione con LangGraph
L'intero processo "mentale" dell'IA è gestito tramite **LangGraph**, una libreria che permette di creare flussi ciclici (grafi) per agenti autonomi.
1. **Stato Condiviso (`AgentState`)**: Ad ogni passo, i nodi leggono e scrivono su una "memoria" centrale tipizzata (il Pydantic TypedDict) che contiene la distinta base, i risultati LCA e la cronologia della conversazione.
2. **Interrupts (Human-in-the-loop)**: Il grafo ha la capacità di "mettersi in pausa". Se l'IA si accorge che mancano informazioni critiche, ferma l'esecuzione e restituisce il controllo alla UI, ponendo domande mirate all'utente per completare il quadro logico (Gap Analysis).
3. **Structured Outputs**: L'IA comunica con il motore Python esclusivamente emettendo JSON validati tramite Pydantic (`agents/schemas.py`). Se il JSON non rispetta le chiavi richieste, viene rifiutato.

## 3. Il Flusso Logico a 7 Passi
L'interazione è strutturata in un ragionamento a imbuto:

1. **Analisi Entità**: Il sistema analizza l'input per capire se l'oggetto in questione è un "Materiale Grezzo" (es. poliuretano puro) o un "Prodotto Complesso" (es. una sedia).
2. **Lookup Aggregato**: Controlla nel dataset LCA se il prodotto intero ha già un'impronta calcolata e nota, bypassando fasi inutili.
3. **Selezione Materiale (Inferenza)**: Se il prodotto è complesso e l'utente non specifica i materiali, l'LLM fa una deduzione tecnica (es. plastica, alluminio) e la registra in una lista pubblica di assunzioni (`assumptions_list`).
4. **Vincolo Geometrico (Mapping)**: L'LLM assegna a ogni componente una Geometria (es. *Corpi Cavi*, *Film*). Il codice Python mappa questa astrazione a un processo di produzione noto (es. *Injection Moulding*).
5. **Scomposizione BOM**: L'oggetto viene scomposto nei suoi sotto-componenti primari, creando la struttura dati della distinta base.
6. **Calcolo Logistica**: Estrazione dei dati geografici. Python calcola il carico logistico ($tkm = (massa\_kg / 1000) \times distanza\_km$). Il sistema controlla che non ci siano doppi conteggi logistici per i dataset di tipo "market".
7. **Validazione e Ricerca Gerarchica**: Se la copertura dati non corrisponde esattamente, interviene la Ricerca Gerarchica Filtrata sul file `DataSet.xlsx`. Questa opera in due fasi:
    - **Product First**: Si esegue un match testuale rigoroso unicamente sul nome del prodotto per garantire coerenza tipologica (es. non si confonde calcestruzzo con arachidi).
    - **Geography Filter**: Si filtra la rosa dei candidati esigendo la medesima Nazione/Area (`target_geography`), accettando fallbacks globali (RER, GLO) solo se strettamente necessari.

## 4. MCDA (Multi-Criteria Decision Analysis)
Dopo aver ideato le alternative più sostenibili, interviene un algoritmo MCDA che stila una classifica oggettiva. Non valuta solo la CO₂, ma peserà:
- **Costo** (€/kg)
- **Energia Implicata** (MJ)
- **Impatto Ambientale** (kg CO₂ eq)
- **Consumo d'Acqua** (L)

Questi pesi determinano la "Migliore Alternativa" bilanciata, mostrando all'utente sia la variante ultra-ecologica, sia quella economica che riduce parzialmente l'impatto.
