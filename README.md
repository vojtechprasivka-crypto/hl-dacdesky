# Hlídač úředních desek

Denní monitoring územního plánování přes **GitHub Actions**, bez vlastního serveru a bez archivu dokumentů. Výchozí zdroj je úřední deska Chotyně včetně archivních záznamů na webu obce.

Výstupy obsahují odkazy na stránku položky i původní PDF, stručné shrnutí, citace s číslem stránky, nejasnosti a připomínky ověřených termínů. PDF a text celé přílohy se po běhu neuchovávají. Pokud obec dokument odstraní, odkaz může přestat fungovat.

## Zapnutí v GitHubu

1. Kód musí být ve výchozí větvi tohoto repozitáře. Povolte **Actions**, pokud GitHub nabízí jejich zapnutí. V repozitáři musí být zapnuté **Issues** (Settings → General → Features).
2. V **Settings → Secrets and variables → Actions → New repository secret** vytvořte **`AI_API_KEY`**. Hodnotou je váš klíč z [Google AI Studio](https://aistudio.google.com/). Klíč nedávejte do kódu ani do chatu.
3. Otevřete **Actions → Monitoring úředních desek → Run workflow**. První běh vytvoří výchozí přehled.
4. Výsledky najdete v přehledu běhu, ve větvi **`monitor-state` → `reports/latest.md`** a při nových nálezech či nevyřízených kontrolách v **Issues**. Odkaz na větev se objeví až po prvním úspěšném uložení.
5. Pro e-mailová upozornění nastavte **Watch → Custom → Issues** a ověřte e-mailová nastavení GitHub účtu. Samotná aplikace nepotřebuje SMTP službu.

Další kontroly se spouštějí automaticky denně přibližně v 06:17 v zimě / 07:17 v létě českého času. GitHub může běh opozdit; nejde o garantovanou službu pro právní lhůty. Schedule funguje z výchozí větve. U neaktivních veřejných repozitářů může GitHub plánované běhy vypnout. Před důležitou lhůtou ověřte originální dokument.

Není nutné zadávat osobní GitHub token: Actions použije vlastní dočasný `GITHUB_TOKEN`. Pokud organizace zakazuje zápis do větví nebo tvorbu Issues, musí správce tato oprávnění povolit. Workflow neobchází pravidla repozitáře.

## Jaké AI a cena

Výchozí poskytovatel je **Gemini API**, model **`gemini-2.5-flash`**. Dostupnost a případná bezplatná kvóta závisí na účtu, modelu a aktuálních podmínkách Googlu. Ověřte je v AI Studio; při neexistujícím modelu nastavte dostupný model proměnnou `AI_MODEL`. Aplikace sama nezapíná placený tarif. Pokud účet má aktivní placené API, volání mohou být zpoplatněná. GitHub Actions má vlastní limity podle tarifu repozitáře.

API se volá jen pro kandidáty územního plánování a novou/nezpracovanou verzi dokumentu, nejvýše 10krát za běh. Neúspěšná analýza zůstane ve stavu čeká a zkusí se znovu při další kontrole. Absence klíče nezastaví sběr. Při velkém množství kandidátů může první zpracování trvat několik dní. Dokumenty s příliš velkým rozsahem zůstanou k ruční kontrole.

AI obdrží text dokumentu, nikoli váš GitHub token. Před použitím ověřte podmínky poskytovatele pro osobní údaje; veřejné dokumenty je mohou obsahovat. Výsledky a citace budou veřejné, je-li veřejný repozitář.

### Jiný poskytovatel

Pod **Settings → Secrets and variables → Actions → Variables** lze nastavit:

| Proměnná | Výchozí hodnota | Význam |
|---|---|---|
| `AI_PROVIDER` | `gemini` | `gemini` nebo `openai` pro OpenAI kompatibilní API |
| `AI_MODEL` | `gemini-2.5-flash` pro Gemini | Model dostupný vašemu účtu; pro `openai` je povinný |
| `AI_BASE_URL` | `https://api.openai.com/v1` | Pro kompatibilní API, například `https://models.github.ai/inference`; bez `/chat/completions` |
| `NOTIFY_ISSUES` | zapnuto | Hodnota `false` vypne vytváření a aktualizaci Issues |

`AI_API_KEY` zůstává vždy Secret. OpenAI kompatibilní poskytovatel musí podporovat chat completions a JSON response format. Ověřená je implementace požadavků a validace se simulovaným API; skutečný účet vybraného poskytovatele vyžaduje váš klíč.

## Přidání obce

Upravte `config/sources.json`. Adaptér `galileo` očekává HTML položky `a.item-href`, detail v `#gcm-main` nebo `main` a přílohy `a.file`. Jiné systémy desek potřebují nový adaptér, nestačí pouze vložit libovolnou URL. Před zapnutím další obce ověřte její strukturu a přílohy.

```json
{
  "id": "chotyne",
  "name": "Chotyně",
  "url": "https://www.chotyne.cz/urad/uredni-deska-1/",
  "lists": [
    "https://www.chotyne.cz/urad/uredni-deska-1/",
    "https://www.chotyne.cz/urad/uredni-deska-1/?show=1"
  ],
  "allowed_hosts": ["www.chotyne.cz"],
  "adapter": "galileo"
}
```

Hosty externích příloh povolujte jednotlivě až po ověření. Nepodporované přílohy, neznámé stránkování, nedostupné stránky či OCR chyby jsou viditelné v reportu. Hlídač nesmí označit výpadek jako „bez změn“.

## Lhůty

Připomínky vznikají **14, 7 a 1 den** před explicitním termínem doloženým citací. Citace musí obsahovat konkrétní úplné datum, jinak termín zůstane neověřený. Relativní právní lhůty aplikace nepočítá. Například u zprávy o uplatňování územního plánu Chotyně může být rozhodné vyvěšení na desce pořizovatele, nikoli datum na obecní desce.

Při pozdním zjištění se vytvoří jedna aktuální připomínka místo všech zmeškaných. Při nahrazení dokumentu se stará analýza přestane používat; nový výsledek obsahuje nové termíny. Kontrola zdroje s chybou připomínky dočasně pozastaví. Uplynulé termíny se nepřipomínají jako budoucí.

GitHub Issue je denní přehled. Opakovaný běh ve stejný den upraví stejnou Issue. E-mailové doručení a jeho načasování řídí GitHub. Automatické ověření citace není právní posouzení ani záruka správnosti AI.

## Jak je uložen stav

Samostatná větev `monitor-state` obsahuje jen metadata, hashe, krátké důkazy, shrnutí, zjištěné lhůty a reporty. Není to archiv dokumentů. Kopie PDF, původní HTML, kompletní OCR výstupy ani API klíče se do ní neukládají. Větev nemažte, jinak systém ztratí paměť předchozích kontrol. Stav se ukládá atomickým Git commitem, ne přes dočasnou cache.

Před odesláním Issue se uloží stav, po odeslání i potvrzení. Přerušení mezi kroky je možné; stejný den se obnovuje stejná Issue, po delším výpadku se může informace opakovat v novém denním přehledu. Není slibováno doručení přesně jednou.

## Lokální spuštění

Vyžaduje Python 3.12 a Tesseract s jazykem `ces`. Na Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-ces
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m app.cli
```

Bez AI klíče report označí kandidáty jako čekající na analýzu. Pro lokální AI nastavte `AI_API_KEY` bezpečně v prostředí, případně `AI_PROVIDER` a `AI_MODEL`. `.env` se automaticky nenačítá. Výsledek: `output/latest.md`, stav: `state/state.json`. Oba adresáře Git ignoruje. Běžné lokální spuštění neposílá zprávy ani nezapisuje do vzdáleného GitHubu.

## Bezpečnost a omezení MVP

HTTPS s ověřováním certifikátů, kontrola hostů/přesměrování, limity velikostí a OCR, citace ověřované proti vstupu. Za cloudovou HTTPS proxy může překlad explicitně povolených hostů provádět proxy. Ve vlastním provozu udržujte závislosti a OCR aktuální. Neznámý či nečitelný dokument vyžaduje kontrolu člověkem.

Nejde o GIS: výskyt parcely v dokumentu neprokazuje změnu jejího využití. Nejsou zpracované všechny existující systémy desek, dokumenty Word/ZIP ani PDF nad limity 50 stran / 8 OCR stran / 20 MB. Pravidlový předvýběr může relevantní dokument minout. AI a souhrny nenahrazují přečtení originálu.
