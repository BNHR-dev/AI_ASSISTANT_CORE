# ROADMAP — AI_ASSISTANT_CORE

## Ligne générale

Priorité actuelle :
**stabiliser, clarifier et exposer proprement le noyau existant avant d’ajouter de nouvelles couches d’orchestration.**

Le projet a déjà franchi le cap critique :
- routeur central lisible
- planner explicite
- executor multi-stratégie
- pipelines web et visuel réels
- observabilité exploitable

La vraie valeur à court terme n’est donc pas un grand refactor, mais un alignement serré entre code, runtime, API et docs.

## Baseline actuelle

Lecture correcte du snapshot :
**V1.7.0 stable / proto V2 contrôlé, avec session 4 visuelle intégrée, runtime post-VM stabilisé et phase 1 de hardening safe backend validée.**

## Ce qui est déjà verrouillé

### Noyau
- décision centrale unique
- planner actif
- traces lisibles
- `build` simple en `single_step`
- web pipeline propre
- visual pipeline propre
- surface `/execute` cohérente
- contrats ComfyUI préservés

### Pipeline Blender
- pipeline Blender fonctionnel côté VM
- génération de `scene.py`, `scene.blend` et `preview.png`
- `scene.blend` comme artefact canonique
- `preview.png` best-effort, produit dans un subprocess séparé
- preview PNG lisible ; qualité visuelle encore améliorable

### Runtime post-VM
- backend canonisé dans la VM via `systemd`
- SearXNG canonisé dans la VM via `systemd` + Docker
- checks runtime réalignés sur la topologie réelle host + VM
- validation reboot de la VM
- répartition runtime clarifiée entre host Windows et VM Hyper-V

### Hardening phase 1
- backend durci via un drop-in `systemd` léger et validé
- hardening safe appliqué sans casser l’exploitation
- sécurité structurelle déjà renforcée par la VM comme frontière produit

## Priorités raisonnables à très court terme

1. **Docs canoniques alignées**
   - `README.md` au root ; `ARCHITECTURE.md`, `ROADMAP.md`, `RUNBOOK_POST_VM.md` dans `docs/`
   - cohérence entre root/README et docs (pas de duplicata des 3 autres fichiers)
   - pack `docs/*` régénéré

2. **Consolidation runtime honnête**
   - documenter le bridge Ollama réel `12001` comme dépendance canonique à court terme
   - garder le firewall host minimal et borné à la VM
   - OpenWebUI acté côté host comme UI opérateur optionnelle, hors runtime canonique (voir `docs/RUNBOOK_POST_VM.md`)

3. **Surface debug plus propre**
   - garder `/health/runtime` comme vue utile
   - nettoyer à terme le marquage “dormant” quand il ne reflète plus l’usage réel

4. **Qualité visible sans refondre le noyau**
   - améliorer prompts et output contracts
   - affiner la sortie build
   - garder la synthèse web nette
   - améliorer encore le confort du pipeline visuel

5. **Legacy sous contrôle**
   - conserver les shims racine passifs
   - éviter toute logique métier nouvelle hors `app/*`

## Étape rentable suivante

Le meilleur prochain move n’est pas une nouvelle couche externe.
C’est une **consolidation documentaire finale + normalisation légère du runtime déclaré** :
- runbook host / VM
- clarification runtime debug
- nettoyage documentaire final
- cohérence des endpoints et exemples de config
- neutralisation des anciens snapshots qui racontent encore `12000`

## Petite phase 2 raisonnable après ça

Une fois la consolidation documentaire terminée, la phase suivante la plus rentable est :
- clarification des profils runtime (`host-only`, `VM canonique`, `UI opérateur host`)
- amélioration légère de la surface debug/runtime si nécessaire
- meilleure lisibilité des exemples `.env` sans refondre le code

## Améliorations Blender possibles

Une fois le pipeline Blender stabilisé en usage, les améliorations les plus naturelles sont :
- qualité visuelle du preview PNG
- meilleurs templates bpy (matériaux, éclairage, composition)
- inspection et validation des scènes générées
- ouverture vers des workflows 3D plus avancés (multi-objets, animations, exports)

## Proto V2 contrôlé

Ce que peut viser le proto V2 sans casser le noyau :
- meilleure qualité visible des sorties
- prompts plus robustes
- outputs plus homogènes
- meilleure exposition UI des agents et des capacités visuelles
- confort supérieur pour les workflows créatifs

## Ce qu’il ne faut pas faire trop tôt

- brancher une nouvelle surcouche d’orchestration avant stabilisation interne
- introduire une mémoire longue non contractée
- multiplier les sélecteurs si `router + planner` suffit déjà
- masquer la dette legacy sous une nouvelle abstraction
- relancer un grand chantier d’architecture alors que le besoin actuel est surtout la cohérence réelle
