# Corpus provenance

Every corpus used in this repository is **third-party and is fetched, not
redistributed**. Only ten example chorales (`scores/corpus/bach_chorales_10/`)
are committed, and those are public-domain Bach used solely as a smoke test.
`scores/corpus/**` is git-ignored; running the fetcher is what populates it.

## Fetching

```bash
python -m score_learning list-sources                 # what is available, and its licence
python -m score_learning fetch --all --accept-license # fetch every corpus
python -m score_learning fetch nottingham --limit 50  # or one source
```

`fetch` writes a `manifest.json` next to each corpus recording the source id,
homepage, licence, fetch timestamp and file count. Sources whose licence
requires acknowledgement (`ack_required = yes` below) refuse to download until
`--accept-license` is passed, and print the licence text first.

## Sources

| id | family / pack | licence | ack required | homepage |
|---|---|---|---|---|
| `beethoven-symphony` | classical/symphony | LicenseRef-CCARH-MuseData | yes | <https://www.ccarh.org/publications/beethoven-symphonies/> |
| `chopin-mazurkas` | dance/mazurka | LicenseRef-CCARH-MuseData | yes | <https://github.com/craigsapp/chopin-mazurkas> |
| `chopin-preludes` | academic/piano-prelude | LicenseRef-CCARH-MuseData | yes | <https://github.com/craigsapp/chopin-preludes> |
| `classical-kern` | classical/classical-sonata | LicenseRef-CCARH-MuseData | yes | CCARH Kern / MuseData |
| `cocopops` | pop/pop-standard | LicenseRef-McGill-Billboard | yes | <https://github.com/Computational-Cognitive-Musicology-Lab/CoCoPops> |
| `haydn-symphony` | classical/symphony | LicenseRef-CCARH-MuseData | yes | <https://github.com/musedata/humdrum-haydn-symphonies> |
| `mctl-symphony` | classical/symphony | MIT | no | <https://github.com/iis-mctl/mctl-symphony-dataset> |
| `nottingham` | folk/british-folk-tune | LicenseRef-Nottingham-NMD | yes | <http://abc.sourceforge.net/NMD> |
| `weimar-jazz` | jazz/swing-standard | ODbL-1.0 | no | <https://jazzomat.hfm-weimar.de/download/download.html> |

## Chorales

The Bach chorales used by every experiment in the paper come from the
Riemenschneider collection **as distributed with music21** (`music21.corpus`,
Bach/chorale), so no download step is needed for them:

```python
import music21
music21.corpus.getComposer('bach')   # 371 chorales; 350 parse to four real parts
```

## Redistribution

`LicenseRef-CCARH-MuseData`, `LicenseRef-McGill-Billboard` and
`LicenseRef-Nottingham-NMD` are not open licences: the upstream projects
restrict redistribution, so the derived **scores** are not shipped. What *is*
shipped is the induced grammars (`skills/composition/genre/packs/*/grammar.yaml`),
which contain counts and probabilities and no musical surface. Those YAML files
are sufficient to reproduce Tables 2, 5, 6, 7, 8 and 9 of the paper and Figures
3--6 without any corpus.
