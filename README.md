# ⚽ Ligue 1 Pronostics

Application de pronostics entre amis pour la Ligue 1.

---

## 📦 Installation rapide

### 1. Prérequis
- Python 3.11 ou plus récent
- pip

### 2. Installer les dépendances

```bash
cd ligue1-pronostics
pip install -r requirements.txt
```

### 3. Lancer l'application

```bash
cd backend
python main.py
```

L'application est accessible sur **http://localhost:8000**

---

## 👥 Connexion

| Utilisateur | Mot de passe initial |
|-------------|----------------------|
| Malherbe | malherbe |
| Ben | ben |
| Seb | seb |
| Coach | coach |
| Ricardo | ricardo |
| Dreux | dreux |
| Mathieu | mathieu |
| La Dame blanche | la dame blanche |
| Le Doubs | le doubs |
| **admin** | **admin123** ← à changer ! |

---

## ⚙️ Configuration

### Clé API-Football (calendrier automatique)

1. Créez un compte gratuit sur [api-football.com](https://www.api-football.com/)
2. Récupérez votre clé API
3. Définissez la variable d'environnement :

```bash
# Linux/Mac
export API_FOOTBALL_KEY="votre_cle_ici"

# Windows
set API_FOOTBALL_KEY=votre_cle_ici
```

Puis relancez l'application.

**Sans clé API** : utilisez la saisie manuelle dans l'interface Admin → Journée → Ajouter un match.

### Variable SECRET_KEY (sécurité sessions)

```bash
export SECRET_KEY="une-chaine-aleatoire-longue-et-secrete"
```

---

## 🚀 Déploiement sur Railway (gratuit)

1. Créez un compte sur [railway.app](https://railway.app)
2. Installez Railway CLI : `npm i -g @railway/cli`
3. Depuis le dossier du projet :

```bash
railway login
railway init
railway up
```

4. Ajoutez les variables d'environnement dans Railway Dashboard :
   - `SECRET_KEY` → une chaîne aléatoire
   - `API_FOOTBALL_KEY` → votre clé API (optionnel)

5. Ajoutez un `Procfile` à la racine :

```
web: cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT
```

Railway détecte automatiquement le fichier `requirements.txt`.

---

## 📊 Système de points

| Label | Points | Condition |
|-------|--------|-----------|
| **BB** Bonus But | 6 pts | Score exact avec 4 buts ou plus |
| **PP** Parfait | 4 pts | Score exact avec moins de 4 buts |
| **PA** Approchant | 3 pts | Bonne issue + écart de buts proche (±1) + total buts proche (±2) |
| **PJ** Juste | 2 pts | Bonne issue uniquement |
| — | 0 pt | Mauvais pronostic |

**Estimation de score** : +2 pts si l'estimation est exactement juste.

**Classement** : Points → PJ → PP → PA (en cas d'égalité)

---

## 🗃️ Base de données

La base SQLite (`backend/pronostics.db`) est créée automatiquement au premier lancement.
Pour réinitialiser : supprimez le fichier `pronostics.db` et relancez.

### Changer un mot de passe (via Python)

```python
import hashlib, sqlite3
conn = sqlite3.connect("backend/pronostics.db")
new_hash = hashlib.sha256("nouveau_mot_de_passe".encode()).hexdigest()
conn.execute("UPDATE users SET password_hash=? WHERE username=?", (new_hash, "Malherbe"))
conn.commit()
conn.close()
```

---

## 📁 Structure du projet

```
ligue1-pronostics/
├── backend/
│   ├── main.py          # Application FastAPI (routes)
│   ├── database.py      # Modèles SQLite + initialisation
│   ├── scoring.py       # Calcul des points (PP/PA/PJ/BB)
│   └── api_football.py  # Intégration API-Football
├── frontend/
│   ├── templates/       # Pages HTML (Jinja2)
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── journee.html
│   │   ├── classement.html
│   │   ├── admin.html
│   │   └── admin_journee.html
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── requirements.txt
└── README.md
```
