#!/usr/bin/env python3
"""
Render special pages (login, register, newsletter) with Jinja2 templates.
These pages need site_url and api_base to work correctly on GitHub Pages.
"""
import os, sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# Configuration
SITE_URL = os.environ.get('EDGE_SITE_URL', 'https://romdmc.github.io/edge')
API_BASE = os.environ.get('EDGE_API_BASE', 'http://72.60.187.136:8081')
TEMPLATES_DIR = Path(__file__).parent / 'templates'
OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('output')

# Minimal i18n
TRANSLATIONS = {
    'fr': {
        'nav_login': 'Connexion', 'nav_register': 'Inscription', 'nav_logout': 'Déconnexion',
        'nav_home': 'Accueil', 'nav_all': 'Tous les articles', 'nav_digest': 'Digest',
        'nav_sources': 'Sources', 'nav_series': 'Séries', 'nav_trends': 'Tendances',
        'nav_archives': 'Archives', 'nav_manifesto': 'Manifeste',
        'login_title': 'Connexion', 'email_label': 'Email', 'password_label': 'Mot de passe',
        'login_btn': 'Se connecter', 'login_success': 'Connexion réussie !', 'login_error': 'Erreur de connexion',
        'login_no_account': 'Pas de compte',
        'register_title': 'Inscription', 'display_name_label': 'Nom', 'email_label': 'Email',
        'password_label': 'Mot de passe', 'password_confirm_label': 'Confirmer le mot de passe',
        'register_btn': "S'inscrire", 'register_success': 'Inscription réussie !',
        'register_error': 'Erreur', 'register_has_account': 'Déjà un compte',
        'newsletter_subscribe': 'Newsletter', 'newsletter_email_placeholder': 'votre@email.com',
        'newsletter_subscribe_btn': "S'abonner", 'newsletter_success': 'Merci pour votre inscription !',
        'newsletter_error': 'Erreur. Veuillez réessayer.', 'newsletter_unsubscribe': 'Désinscription',
        'newsletter_unsubscribed': 'Vous êtes désinscrit.', 'back_to_home': "Retour à l'accueil",
        'footer_tagline': 'Veille tech auto-améliorant', 'generated_on': 'Généré le',
        'footer_incubated_by': 'Projet incubé par',
    },
    'en': {
        'nav_login': 'Login', 'nav_register': 'Register', 'nav_logout': 'Logout',
        'nav_home': 'Home', 'nav_all': 'All articles', 'nav_digest': 'Digest',
        'nav_sources': 'Sources', 'nav_series': 'Series', 'nav_trends': 'Trends',
        'nav_archives': 'Archives', 'nav_manifesto': 'Manifesto',
        'login_title': 'Login', 'email_label': 'Email', 'password_label': 'Password',
        'login_btn': 'Sign in', 'login_success': 'Logged in!', 'login_error': 'Login error',
        'login_no_account': 'No account',
        'register_title': 'Register', 'display_name_label': 'Name', 'email_label': 'Email',
        'password_label': 'Password', 'password_confirm_label': 'Confirm password',
        'register_btn': 'Sign up', 'register_success': 'Registered!',
        'register_error': 'Error', 'register_has_account': 'Already have an account',
        'newsletter_subscribe': 'Newsletter', 'newsletter_email_placeholder': 'your@email.com',
        'newsletter_subscribe_btn': 'Subscribe', 'newsletter_success': 'Thanks for subscribing!',
        'newsletter_error': 'Error. Please try again.', 'newsletter_unsubscribe': 'Unsubscribe',
        'newsletter_unsubscribed': 'You have been unsubscribed.', 'back_to_home': 'Back to home',
        'footer_tagline': 'Self-improving tech news', 'generated_on': 'Generated on',
        'footer_incubated_by': 'Incubated by',
    }
}

def t(key, lang='fr', **kw):
    return TRANSLATIONS.get(lang, TRANSLATIONS['fr']).get(key, key)

# Jinja2 env
env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
env.globals['site_url'] = SITE_URL
env.globals['api_base'] = API_BASE
env.globals['t'] = t
env.globals['lang'] = 'fr'
env.globals['active_page'] = ''
env.globals['generated_at'] = ''

# Pages to render
PAGES = [
    ('login.html', 'login.html', {'active_page': 'login'}),
    ('register.html', 'register.html', {'active_page': 'register'}),
    ('newsletter_subscribe.html', 'newsletter_subscribe.html', {'active_page': 'newsletter'}),
    ('newsletter_unsubscribe.html', 'newsletter_unsubscribe.html', {'active_page': 'newsletter'}),
]

for template_name, output_name, extra_ctx in PAGES:
    try:
        tmpl = env.get_template(template_name)
        html = tmpl.render(**extra_ctx)
        out_path = OUTPUT_DIR / output_name
        out_path.write_text(html, encoding='utf-8')
        print(f'Rendered: {output_name}')
    except Exception as e:
        print(f'Error rendering {template_name}: {e}')

print('Done')
