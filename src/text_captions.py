"""On-screen text overlays + captions (SRT + styled ASS).

Overlays are rendered through libass (a top-positioned ASS style) instead of
FFmpeg drawtext, so long text, Unicode, commas, colons, quotes and
apostrophes can never break a filter graph.
"""
import os
import re

from .ffmpeg_util import sanitize_ass_text


# ---------------------------------------------------------------------------
# Caption templates (CapCut viral/trending styles).
# Each template defines an ASS "Cap" style. Colours are ASS &HAABBGGRR.
# size_scale multiplies the base font size. box=True draws an opaque
# background box behind the text (BorderStyle 3).
# ---------------------------------------------------------------------------
CAPTION_TEMPLATES = {'tiktok_classic': {'label': 'TikTok Classic',
                    'category': 'Trending',
                    'desc': 'White bold caps, black outline, yellow word highlight — the viral '
                            'standard',
                    'font': 'Montserrat',
                    'size_scale': 1.0,
                    'primary': '&H00FFFFFF',
                    'secondary': '&H0000D7FF',
                    'outline_c': '&H90000000',
                    'back_c': '&H90000000',
                    'bold': -1,
                    'italic': 0,
                    'outline': 3,
                    'shadow': 1,
                    'alignment': 2,
                    'box': False,
                    'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                    'text_case': 'upper'},
 'hormozi_yellow': {'label': 'Hormozi Yellow',
                    'category': 'Trending',
                    'desc': 'Condensed caps, thick black stroke, yellow spoken word',
                    'font': 'Anton',
                    'size_scale': 1.05,
                    'primary': '&H00FFFFFF',
                    'secondary': '&H0000D7FF',
                    'outline_c': '&H00000000',
                    'back_c': '&H00000000',
                    'bold': 0,
                    'italic': 0,
                    'outline': 4,
                    'shadow': 1,
                    'alignment': 2,
                    'box': False,
                    'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                    'text_case': 'upper'},
 'hormozi_cyan': {'label': 'Hormozi Cyan',
                  'category': 'Trending',
                  'desc': 'Condensed caps, thick black stroke, cyan spoken word',
                  'font': 'Anton',
                  'size_scale': 1.05,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H00FFD900',
                  'outline_c': '&H00000000',
                  'back_c': '&H00000000',
                  'bold': 0,
                  'italic': 0,
                  'outline': 4,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                  'text_case': 'upper'},
 'hormozi_green': {'label': 'Hormozi Green',
                   'category': 'Trending',
                   'desc': 'Condensed caps, thick black stroke, green spoken word',
                   'font': 'Anton',
                   'size_scale': 1.05,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0076E600',
                   'outline_c': '&H00000000',
                   'back_c': '&H00000000',
                   'bold': 0,
                   'italic': 0,
                   'outline': 4,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'upper'},
 'hormozi_orange': {'label': 'Hormozi Orange',
                    'category': 'Trending',
                    'desc': 'Condensed caps, thick black stroke, orange spoken word',
                    'font': 'Anton',
                    'size_scale': 1.05,
                    'primary': '&H00FFFFFF',
                    'secondary': '&H000066FF',
                    'outline_c': '&H00000000',
                    'back_c': '&H00000000',
                    'bold': 0,
                    'italic': 0,
                    'outline': 4,
                    'shadow': 1,
                    'alignment': 2,
                    'box': False,
                    'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                    'text_case': 'upper'},
 'hormozi_pink': {'label': 'Hormozi Pink',
                  'category': 'Trending',
                  'desc': 'Condensed caps, thick black stroke, hot-pink spoken word',
                  'font': 'Anton',
                  'size_scale': 1.05,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H009948EC',
                  'outline_c': '&H00000000',
                  'back_c': '&H00000000',
                  'bold': 0,
                  'italic': 0,
                  'outline': 4,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                  'text_case': 'upper'},
 'hormozi_red': {'label': 'Hormozi Red',
                 'category': 'Trending',
                 'desc': 'Condensed caps, thick black stroke, red spoken word',
                 'font': 'Anton',
                 'size_scale': 1.05,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H000000FF',
                 'outline_c': '&H00000000',
                 'back_c': '&H00000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 4,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper'},
 'mrbeast': {'label': 'MrBeast',
             'category': 'Trending',
             'desc': 'Huge cartoon caps, yellow text, heavy outline, orange pop word',
             'font': 'Bangers',
             'size_scale': 1.35,
             'primary': '&H0000D7FF',
             'secondary': '&H000066FF',
             'outline_c': '&H00000000',
             'back_c': '&H00000000',
             'bold': 0,
             'italic': 0,
             'outline': 5,
             'shadow': 2,
             'alignment': 2,
             'box': False,
             'highlight_mode': 'wordbig',
                      'anim': 'pop', 'entrance': 'slideup',
             'text_case': 'upper',
             'big_scale': 1.5},
 'podcast_viral': {'label': 'Podcast Viral',
                   'category': 'Trending',
                   'desc': 'Bold caps, white text, electric lime spoken word — interview clips',
                   'font': 'Montserrat',
                   'size_scale': 1.0,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0035E6A3',
                   'outline_c': '&H90000000',
                   'back_c': '&H90000000',
                   'bold': -1,
                   'italic': 0,
                   'outline': 3,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'upper'},
 'kapwing_viral': {'label': 'Kapwing Viral',
                   'category': 'Trending',
                   'desc': 'Bold caps, white text, vibrant blue spoken word',
                   'font': 'Montserrat',
                   'size_scale': 1.0,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H00EB6325',
                   'outline_c': '&H90000000',
                   'back_c': '&H90000000',
                   'bold': -1,
                   'italic': 0,
                   'outline': 3,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'upper'},
 'submagic': {'label': 'Submagic',
              'category': 'Trending',
              'desc': 'Cream text, vivid purple spoken word — storytelling reels',
              'font': 'Poppins',
              'size_scale': 1.0,
              'primary': '&H00C3F9FE',
              'secondary': '&H00F755A8',
              'outline_c': '&H90000000',
              'back_c': '&H90000000',
              'bold': -1,
              'italic': 0,
              'outline': 2,
              'shadow': 1,
              'alignment': 2,
              'box': False,
              'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
              'text_case': 'mixed'},
 'beast_word': {'label': 'Beast Word',
                'category': 'Trending',
                'desc': 'Condensed caps, white text, red giant pop word',
                'font': 'Anton',
                'size_scale': 1.1,
                'primary': '&H00FFFFFF',
                'secondary': '&H000000FF',
                'outline_c': '&H00000000',
                'back_c': '&H00000000',
                'bold': 0,
                'italic': 0,
                'outline': 4,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'wordbig',
                      'anim': 'pop', 'entrance': 'slideup',
                'text_case': 'upper',
                'big_scale': 1.7},
 'neon_cyan': {'label': 'Neon Cyan',
               'category': 'Glow',
               'desc': 'White text, cyan glowing spoken word — night/gaming vibe',
               'font': 'Montserrat',
               'size_scale': 1.05,
               'primary': '&H00FFFFFF',
               'secondary': '&H00FFFF00',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H00FFFF00'},
 'neon_pink': {'label': 'Neon Pink',
               'category': 'Glow',
               'desc': 'White text, hot-pink glowing spoken word',
               'font': 'Montserrat',
               'size_scale': 1.05,
               'primary': '&H00FFFFFF',
               'secondary': '&H005500FF',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H005500FF'},
 'neon_lime': {'label': 'Neon Lime',
               'category': 'Glow',
               'desc': 'White text, lime glowing spoken word',
               'font': 'Montserrat',
               'size_scale': 1.05,
               'primary': '&H00FFFFFF',
               'secondary': '&H0035E6A3',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H0035E6A3'},
 'neon_gold': {'label': 'Neon Gold',
               'category': 'Glow',
               'desc': 'White text, gold glowing spoken word',
               'font': 'Montserrat',
               'size_scale': 1.05,
               'primary': '&H00FFFFFF',
               'secondary': '&H0000D7FF',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H0000D7FF'},
 'cyberpunk': {'label': 'Cyberpunk',
               'category': 'Glow',
               'desc': 'Cyan text, neon magenta glowing word — gaming/sci-fi',
               'font': 'Outfit',
               'size_scale': 1.0,
               'primary': '&H00EED322',
               'secondary': '&H005E3FF4',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H005E3FF4'},
 'dark_psychology': {'label': 'Dark Psychology',
                     'category': 'Glow',
                     'desc': 'White text, blood-red glowing word — thriller/philosophy',
                     'font': 'Outfit',
                     'size_scale': 1.0,
                     'primary': '&H00FFFFFF',
                     'secondary': '&H002626DC',
                     'outline_c': '&H90000000',
                     'back_c': '&H90000000',
                     'bold': -1,
                     'italic': 0,
                     'outline': 2,
                     'shadow': 1,
                     'alignment': 2,
                     'box': False,
                     'highlight_mode': 'wordglow',
                      'anim': 'shake', 'entrance': 'slideup',
                     'text_case': 'upper',
                     'glow_color': '&H002626DC'},
 'fintech': {'label': 'Fintech Wealth',
             'category': 'Glow',
             'desc': 'Fluorescent text, emerald glowing word — finance/crypto',
             'font': 'Outfit',
             'size_scale': 1.0,
             'primary': '&H00FFE7E0',
             'secondary': '&H0081B910',
             'outline_c': '&H90000000',
             'back_c': '&H90000000',
             'bold': -1,
             'italic': 0,
             'outline': 2,
             'shadow': 1,
             'alignment': 2,
             'box': False,
             'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
             'text_case': 'upper',
             'glow_color': '&H0081B910'},
 'rainbow_glow': {'label': 'Rainbow Glow',
                  'category': 'Glow',
                  'desc': 'White text, magenta-cyan glowing spoken word',
                  'font': 'Poppins',
                  'size_scale': 1.05,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H00FFFFFF',
                  'outline_c': '&H90000000',
                  'back_c': '&H90000000',
                  'bold': -1,
                  'italic': 0,
                  'outline': 2,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
                  'text_case': 'upper',
                  'glow_color': '&H00F755A8'},
 'neon_tube': {'label': 'Neon Tube',
               'category': 'Glow',
               'desc': 'Glowing pink neon-tube caps on dark',
               'font': 'Bebas Neue',
               'size_scale': 1.15,
               'primary': '&H005500FF',
               'secondary': '&H00FFFF00',
               'outline_c': '&H005500FF',
               'back_c': '&H90000000',
               'bold': 0,
               'italic': 0,
               'outline': 2,
               'shadow': 3,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordglow',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'glow_color': '&H005500FF'},
 'ali_abdaal': {'label': 'Ali Abdaal',
                'category': 'Aesthetic',
                'desc': 'Soft cream text, pastel blue word — productivity/study',
                'font': 'Poppins',
                'size_scale': 0.9,
                'primary': '&H00EBFBFF',
                'secondary': '&H00F8BD38',
                'outline_c': '&H80000000',
                'back_c': '&H80000000',
                'bold': 0,
                'italic': 0,
                'outline': 1,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                'text_case': 'mixed'},
 'iman_luxury': {'label': 'Iman Luxury',
                 'category': 'Aesthetic',
                 'desc': 'Off-white text, champagne gold word — luxury/cinematic',
                 'font': 'Poppins',
                 'size_scale': 0.95,
                 'primary': '&H00FCFAF8',
                 'secondary': '&H000677D9',
                 'outline_c': '&H80000000',
                 'back_c': '&H80000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                 'text_case': 'mixed'},
 'cinema_noir': {'label': 'Cinema Noir',
                 'category': 'Aesthetic',
                 'desc': 'Silver text, white word — dramatic cinematic',
                 'font': 'Montserrat',
                 'size_scale': 0.9,
                 'primary': '&H00F0E8E2',
                 'secondary': '&H00FFFFFF',
                 'outline_c': '&H80000000',
                 'back_c': '&H80000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                 'text_case': 'mixed'},
 'vox_explainer': {'label': 'Vox Explainer',
                   'category': 'Aesthetic',
                   'desc': 'Off-white text, bright yellow word — video essays',
                   'font': 'Poppins',
                   'size_scale': 0.9,
                   'primary': '&H00FCFAF8',
                   'secondary': '&H0024BFFB',
                   'outline_c': '&H80000000',
                   'back_c': '&H80000000',
                   'bold': 0,
                   'italic': 0,
                   'outline': 1,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                   'text_case': 'mixed'},
 'cinema_gold': {'label': 'Cinema Gold',
                 'category': 'Aesthetic',
                 'desc': 'Off-white text, vintage gold word — cinematic shorts',
                 'font': 'Poppins',
                 'size_scale': 0.9,
                 'primary': '&H00FAFAFA',
                 'secondary': '&H00048ACA',
                 'outline_c': '&H80000000',
                 'back_c': '&H80000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                 'text_case': 'mixed'},
 'elegant_serif': {'label': 'Elegant Serif',
                   'category': 'Aesthetic',
                   'desc': 'Refined serif, gold word — elegant minimal',
                   'font': 'Georgia',
                   'size_scale': 0.9,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0000D7FF',
                   'outline_c': '&H80000000',
                   'back_c': '&H80000000',
                   'bold': 0,
                   'italic': 1,
                   'outline': 1,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                   'text_case': 'mixed'},
 'nordic_clean': {'label': 'Nordic Clean',
                  'category': 'Aesthetic',
                  'desc': 'White text, slate word — calm minimal design',
                  'font': 'Outfit',
                  'size_scale': 0.9,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H00B8A394',
                  'outline_c': '&H80000000',
                  'back_c': '&H80000000',
                  'bold': 0,
                  'italic': 0,
                  'outline': 1,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                  'text_case': 'mixed'},
 'clean_tech': {'label': 'Clean Tech',
                'category': 'Aesthetic',
                'desc': 'White text, electric blue word — SaaS/product',
                'font': 'Outfit',
                'size_scale': 0.9,
                'primary': '&H00FFFFFF',
                'secondary': '&H00F8BD38',
                'outline_c': '&H80000000',
                'back_c': '&H80000000',
                'bold': 0,
                'italic': 0,
                'outline': 1,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                'text_case': 'mixed'},
 'script_accent': {'label': 'Script Accent',
                   'category': 'Aesthetic',
                   'desc': 'Clean sans text, gold handwritten accent word',
                   'font': 'Poppins',
                   'size_scale': 0.9,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0000D7FF',
                   'outline_c': '&H80000000',
                   'back_c': '&H80000000',
                   'bold': 0,
                   'italic': 0,
                   'outline': 1,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'mixed'},
 'purple_pill': {'label': 'Purple Pill',
                 'category': 'Highlight',
                 'desc': 'White caps, spoken word on purple pill',
                 'font': 'Montserrat',
                 'size_scale': 1.0,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H00FFFFFF',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': -1,
                 'italic': 0,
                 'outline': 2,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper',
                 'box_color': '&H00DC279B'},
 'yellow_pill': {'label': 'Yellow Pill',
                 'category': 'Highlight',
                 'desc': 'White caps, spoken word on yellow pill',
                 'font': 'Montserrat',
                 'size_scale': 1.0,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H00000000',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': -1,
                 'italic': 0,
                 'outline': 2,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper',
                 'box_color': '&H0000D7FF'},
 'red_pill': {'label': 'Red Pill',
              'category': 'Highlight',
              'desc': 'White caps, spoken word on red pill',
              'font': 'Montserrat',
              'size_scale': 1.0,
              'primary': '&H00FFFFFF',
              'secondary': '&H00FFFFFF',
              'outline_c': '&H90000000',
              'back_c': '&H90000000',
              'bold': -1,
              'italic': 0,
              'outline': 2,
              'shadow': 1,
              'alignment': 2,
              'box': False,
              'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
              'text_case': 'upper',
              'box_color': '&H002626DC'},
 'blue_pill': {'label': 'Blue Pill',
               'category': 'Highlight',
               'desc': 'White caps, spoken word on blue pill',
               'font': 'Montserrat',
               'size_scale': 1.0,
               'primary': '&H00FFFFFF',
               'secondary': '&H00FFFFFF',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'box_color': '&H00EB6325'},
 'green_pill': {'label': 'Green Pill',
                'category': 'Highlight',
                'desc': 'White caps, spoken word on green pill',
                'font': 'Montserrat',
                'size_scale': 1.0,
                'primary': '&H00FFFFFF',
                'secondary': '&H00000000',
                'outline_c': '&H90000000',
                'back_c': '&H90000000',
                'bold': -1,
                'italic': 0,
                'outline': 2,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                'text_case': 'upper',
                'box_color': '&H0035E6A3'},
 'black_pill': {'label': 'Black Pill',
                'category': 'Highlight',
                'desc': 'White caps, spoken word on black pill',
                'font': 'Montserrat',
                'size_scale': 1.0,
                'primary': '&H00FFFFFF',
                'secondary': '&H0000D7FF',
                'outline_c': '&H90000000',
                'back_c': '&H90000000',
                'bold': -1,
                'italic': 0,
                'outline': 2,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                'text_case': 'upper',
                'box_color': '&H00000000'},
 'cyan_pill': {'label': 'Cyan Pill',
               'category': 'Highlight',
               'desc': 'White caps, spoken word on cyan pill',
               'font': 'Montserrat',
               'size_scale': 1.0,
               'primary': '&H00FFFFFF',
               'secondary': '&H00000000',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 2,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
               'text_case': 'upper',
               'box_color': '&H00FFFF00'},
 'highlighter_yellow': {'label': 'Highlighter Yellow',
                        'category': 'Highlight',
                        'desc': 'Marker swipe behind spoken word — study/notes vibe',
                        'font': 'Poppins',
                        'size_scale': 1.0,
                        'primary': '&H00FFFFFF',
                        'secondary': '&H00000000',
                        'outline_c': '&H90000000',
                        'back_c': '&H90000000',
                        'bold': -1,
                        'italic': 0,
                        'outline': 2,
                        'shadow': 1,
                        'alignment': 2,
                        'box': False,
                        'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                        'text_case': 'mixed',
                        'box_color': '&H0000D7FF'},
 'highlighter_pink': {'label': 'Highlighter Pink',
                      'category': 'Highlight',
                      'desc': 'Pink marker swipe behind spoken word',
                      'font': 'Poppins',
                      'size_scale': 1.0,
                      'primary': '&H00FFFFFF',
                      'secondary': '&H00000000',
                      'outline_c': '&H90000000',
                      'back_c': '&H90000000',
                      'bold': -1,
                      'italic': 0,
                      'outline': 2,
                      'shadow': 1,
                      'alignment': 2,
                      'box': False,
                      'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                      'text_case': 'mixed',
                      'box_color': '&H009948EC'},
 'red_box_sweep': {'label': 'Red Box Sweep',
                   'category': 'Highlight',
                   'desc': 'Off-white caps, red box sweeps behind spoken word',
                   'font': 'Outfit',
                   'size_scale': 1.0,
                   'primary': '&H00FAF6F4',
                   'secondary': '&H00FFFFFF',
                   'outline_c': '&H90000000',
                   'back_c': '&H90000000',
                   'bold': -1,
                   'italic': 0,
                   'outline': 2,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'wordbox',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'upper',
                   'box_color': '&H004F38E5'},
 'karaoke_fill': {'label': 'Karaoke Fill',
                  'category': 'Highlight',
                  'desc': 'True sing-along: yellow wipes across each word as spoken',
                  'font': 'Montserrat',
                  'size_scale': 1.0,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H000AD6FF',
                  'outline_c': '&H90000000',
                  'back_c': '&H90000000',
                  'bold': -1,
                  'italic': 0,
                  'outline': 2,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'karaoke', 'karaoke_smooth': True,
                      'anim': 'none', 'entrance': 'slideup',
                  'text_case': 'upper'},
 'word_big_pop': {'label': 'Word Big Pop',
                  'category': 'Highlight',
                  'desc': 'Spoken word pops bigger + colored',
                  'font': 'Anton',
                  'size_scale': 1.05,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H0000D7FF',
                  'outline_c': '&H00000000',
                  'back_c': '&H00000000',
                  'bold': 0,
                  'italic': 0,
                  'outline': 4,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'wordbig',
                      'anim': 'bounce', 'entrance': 'slideup',
                  'text_case': 'upper',
                  'big_scale': 1.8},
 'word_pop': {'label': 'Word Pop',
              'category': 'Word',
              'desc': 'Bold caps karaoke, yellow word — high-energy hooks',
              'font': 'Anton',
              'size_scale': 1.05,
              'primary': '&H00FFFFFF',
              'secondary': '&H0000D7FF',
              'outline_c': '&H00000000',
              'back_c': '&H00000000',
              'bold': 0,
              'italic': 0,
              'outline': 4,
              'shadow': 1,
              'alignment': 2,
              'box': False,
              'highlight_mode': 'word',
                      'anim': 'bounce', 'entrance': 'slideup',
              'text_case': 'upper'},
 'single_word_flash': {'label': 'Single Word Flash',
                       'category': 'Word',
                       'desc': 'One giant word at a time — Hormozi/MrBeast style',
                       'font': 'Anton',
                       'size_scale': 1.5,
                       'primary': '&H00FFFFFF',
                       'secondary': '&H0000D7FF',
                       'outline_c': '&H00000000',
                       'back_c': '&H00000000',
                       'bold': 0,
                       'italic': 0,
                       'outline': 5,
                       'shadow': 2,
                       'alignment': 2,
                       'box': False,
                       'highlight_mode': 'wordbig',
                      'anim': 'pop', 'entrance': 'slideup',
                       'text_case': 'upper',
                       'big_scale': 1.3},
 'scale_pop': {'label': 'Scale Pop',
               'category': 'Word',
               'desc': 'Spoken word scales up big + cyan',
               'font': 'Montserrat',
               'size_scale': 1.0,
               'primary': '&H00FFFFFF',
               'secondary': '&H00FFFF00',
               'outline_c': '&H90000000',
               'back_c': '&H90000000',
               'bold': -1,
               'italic': 0,
               'outline': 3,
               'shadow': 1,
               'alignment': 2,
               'box': False,
               'highlight_mode': 'wordbig',
                      'anim': 'bounce', 'entrance': 'slideup',
               'text_case': 'upper',
               'big_scale': 1.6},
 'typewriter': {'label': 'Typewriter',
                'category': 'Word',
                'desc': 'Monospace white on black blocks — terminal vibe',
                'font': 'Courier Prime',
                'size_scale': 0.95,
                'primary': '&H00FFFFFF',
                'secondary': '&H0000D7FF',
                'outline_c': '&H00000000',
                'back_c': '&HC8000000',
                'bold': -1,
                'italic': 0,
                'outline': 1,
                'shadow': 0,
                'alignment': 2,
                'box': True,
                'highlight_mode': 'typewriter',
                      'anim': 'none', 'entrance': 'none',
                'text_case': 'mixed'},
 'code_terminal': {'label': 'Code Terminal',
                   'category': 'Word',
                   'desc': 'Terminal white, matrix-green word — dev tutorials',
                   'font': 'Courier Prime',
                   'size_scale': 0.95,
                   'primary': '&H00F9F5F1',
                   'secondary': '&H005EC522',
                   'outline_c': '&H00000000',
                   'back_c': '&HC8000000',
                   'bold': -1,
                   'italic': 0,
                   'outline': 1,
                   'shadow': 0,
                   'alignment': 2,
                   'box': True,
                   'highlight_mode': 'typewriter',
                      'anim': 'pop', 'entrance': 'none',
                   'text_case': 'mixed'},
 'hormozi_box': {'label': 'Hormozi Box',
                 'category': 'Frame',
                 'desc': 'White caps on solid black box — Alex Hormozi style',
                 'font': 'Anton',
                 'size_scale': 0.95,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H0000D7FF',
                 'outline_c': '&H00000000',
                 'back_c': '&HC8000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 0,
                 'alignment': 2,
                 'box': True,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper'},
 'blue_box': {'label': 'Blue Box',
              'category': 'Frame',
              'desc': 'White caps on solid blue box',
              'font': 'Montserrat',
              'size_scale': 0.95,
              'primary': '&H00FFFFFF',
              'secondary': '&H0000D7FF',
              'outline_c': '&H00000000',
              'back_c': '&H00C02800',
              'bold': -1,
              'italic': 0,
              'outline': 1,
              'shadow': 0,
              'alignment': 2,
              'box': True,
              'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
              'text_case': 'upper'},
 'red_box': {'label': 'Red Box',
             'category': 'Frame',
             'desc': 'White caps on solid red box',
             'font': 'Montserrat',
             'size_scale': 0.95,
             'primary': '&H00FFFFFF',
             'secondary': '&H0000D7FF',
             'outline_c': '&H00000000',
             'back_c': '&H002626DC',
             'bold': -1,
             'italic': 0,
             'outline': 1,
             'shadow': 0,
             'alignment': 2,
             'box': True,
             'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
             'text_case': 'upper'},
 'caption_bar': {'label': 'Caption Bar',
                 'category': 'Frame',
                 'desc': 'White text on rounded dark bar — podcast style',
                 'font': 'Poppins',
                 'size_scale': 0.9,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H0000D7FF',
                 'outline_c': '&H00000000',
                 'back_c': '&HEB141410',
                 'bold': -1,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 0,
                 'alignment': 2,
                 'box': False,
                 'pill': True,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'fadein',
                 'text_case': 'mixed'},
 'news_bar': {'label': 'News Bar',
              'category': 'Frame',
              'desc': 'Broadcast lower-third bar with headline',
              'font': 'Poppins',
              'size_scale': 0.9,
              'primary': '&H00FFFFFF',
              'secondary': '&H0000D7FF',
              'outline_c': '&H00000000',
              'back_c': '&HF0141414',
              'bold': -1,
              'italic': 0,
              'outline': 1,
              'shadow': 0,
              'alignment': 2,
              'box': True,
              'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'fadein',
              'text_case': 'upper'},
 'tiktok_bar': {'label': 'TikTok Bar',
                'category': 'Frame',
                'desc': 'White text on translucent black bar — classic TikTok',
                'font': 'Poppins',
                'size_scale': 0.9,
                'primary': '&H00FFFFFF',
                'secondary': '&H0000D7FF',
                'outline_c': '&H00000000',
                'back_c': '&H99000000',
                'bold': 0,
                'italic': 0,
                'outline': 1,
                'shadow': 0,
                'alignment': 2,
                'box': False,
                'pill': True,
                'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'fadein',
                'text_case': 'mixed'},
 'white_card': {'label': 'White Card',
                'category': 'Frame',
                'desc': 'Black bold text on white rounded pill',
                'font': 'Poppins',
                'size_scale': 0.9,
                'primary': '&H00000000',
                'secondary': '&H00DC279B',
                'outline_c': '&H00000000',
                'back_c': '&H00FFFFFF',
                'bold': -1,
                'italic': 0,
                'outline': 1,
                'shadow': 0,
                'alignment': 2,
                'box': False,
                'pill': True,
                'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                'text_case': 'mixed'},
 'black_contour': {'label': 'Black Contour',
                   'category': 'Frame',
                   'desc': 'White bold text on solid black pills',
                   'font': 'Poppins',
                   'size_scale': 0.9,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0000D7FF',
                   'outline_c': '&H00000000',
                   'back_c': '&H00000000',
                   'bold': -1,
                   'italic': 0,
                   'outline': 1,
                   'shadow': 0,
                   'alignment': 2,
                   'box': False,
                   'pill': True,
                   'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                   'text_case': 'upper'},
 'minimal_vlog_capsule': {'label': 'Vlog Capsule',
                          'category': 'Frame',
                          'desc': 'Frosted black capsule, tracked uppercase',
                          'font': 'Outfit',
                          'size_scale': 0.85,
                          'primary': '&H00FFFFFF',
                          'secondary': '&H00F8BD38',
                          'outline_c': '&H00000000',
                          'back_c': '&HEB141410',
                          'bold': 0,
                          'italic': 0,
                          'outline': 1,
                          'shadow': 0,
                          'alignment': 2,
                          'box': False,
                          'pill': True,
                          'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                          'text_case': 'upper'},
 'minimal_clean': {'label': 'Minimal Clean',
                   'category': 'Monoline',
                   'desc': 'Small clean text, no stroke — cinematic feel',
                   'font': 'Poppins',
                   'size_scale': 0.7,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H0000D7FF',
                   'outline_c': '&H80000000',
                   'back_c': '&H80000000',
                   'bold': 0,
                   'italic': 0,
                   'outline': 0,
                   'shadow': 1,
                   'alignment': 2,
                   'box': False,
                   'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                   'text_case': 'mixed'},
 'youtube_style': {'label': 'YouTube Style',
                   'category': 'Monoline',
                   'desc': 'Standard YouTube captions — white on translucent black',
                   'font': 'Arial',
                   'size_scale': 0.85,
                   'primary': '&H00FFFFFF',
                   'secondary': '&H00FFFFFF',
                   'outline_c': '&H00000000',
                   'back_c': '&H99000000',
                   'bold': 0,
                   'italic': 0,
                   'outline': 1,
                   'shadow': 0,
                   'alignment': 2,
                   'box': True,
                   'highlight_mode': 'karaoke',
                      'anim': 'none', 'entrance': 'fadein',
                   'text_case': 'mixed'},
 'outline_only': {'label': 'Outline Only',
                  'category': 'Monoline',
                  'desc': 'Hollow outlined caps, no fill',
                  'font': 'Outfit',
                  'size_scale': 1.0,
                  'primary': '&HFFFFFFFF',
                  'secondary': '&H0000D7FF',
                  'outline_c': '&H00FFFFFF',
                  'back_c': '&H80000000',
                  'bold': -1,
                  'italic': 0,
                  'outline': 3,
                  'shadow': 0,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                  'text_case': 'upper'},
 'thin_clean': {'label': 'Thin Clean',
                'category': 'Monoline',
                'desc': 'Thin white text, subtle — documentary lower third',
                'font': 'Outfit',
                'size_scale': 0.8,
                'primary': '&H00FFFFFF',
                'secondary': '&H0035E6A3',
                'outline_c': '&H80000000',
                'back_c': '&H80000000',
                'bold': 0,
                'italic': 0,
                'outline': 1,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                'text_case': 'mixed'},
 'meme_top': {'label': 'Meme Top',
              'category': 'Basic',
              'desc': 'Classic Impact meme caps at top of frame',
              'font': 'Impact',
              'size_scale': 1.1,
              'primary': '&H00FFFFFF',
              'secondary': '&H0000D7FF',
              'outline_c': '&H90000000',
              'back_c': '&H90000000',
              'bold': 0,
              'italic': 0,
              'outline': 4,
              'shadow': 0,
              'alignment': 8,
              'box': False,
              'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'fadein',
              'text_case': 'upper'},
 'meme_bottom': {'label': 'Meme Bottom',
                 'category': 'Basic',
                 'desc': 'Classic Impact meme caps at bottom',
                 'font': 'Impact',
                 'size_scale': 1.1,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H0000D7FF',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 4,
                 'shadow': 0,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'fadein',
                 'text_case': 'upper'},
 'comic': {'label': 'Comic',
           'category': 'Basic',
           'desc': 'Cartoon font, yellow word pop',
           'font': 'Fredoka',
           'size_scale': 1.05,
           'primary': '&H00FFFFFF',
           'secondary': '&H0000D7FF',
           'outline_c': '&H90000000',
           'back_c': '&H90000000',
           'bold': -1,
           'italic': 0,
           'outline': 3,
           'shadow': 1,
           'alignment': 2,
           'box': False,
           'highlight_mode': 'word',
                      'anim': 'bounce', 'entrance': 'slideup',
           'text_case': 'mixed'},
 'comic_pink': {'label': 'Comic Pink',
                'category': 'Basic',
                'desc': 'Cartoon font, pink word pop',
                'font': 'Fredoka',
                'size_scale': 1.05,
                'primary': '&H00FFFFFF',
                'secondary': '&H009948EC',
                'outline_c': '&H90000000',
                'back_c': '&H90000000',
                'bold': -1,
                'italic': 0,
                'outline': 3,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'word',
                      'anim': 'bounce', 'entrance': 'slideup',
                'text_case': 'mixed'},
 'retro': {'label': 'Retro',
           'category': 'Basic',
           'desc': 'Vintage warm tones, bold caps',
           'font': 'Poppins',
           'size_scale': 1.0,
           'primary': '&H008AF0FE',
           'secondary': '&H00048ACA',
           'outline_c': '&H90441A00',
           'back_c': '&H90441A00',
           'bold': -1,
           'italic': 0,
           'outline': 2,
           'shadow': 1,
           'alignment': 2,
           'box': False,
           'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
           'text_case': 'upper'},
 'urban_rebel': {'label': 'Urban Rebel',
                 'category': 'Basic',
                 'desc': 'Pale yellow caps, red word — streetwear/hip-hop',
                 'font': 'Anton',
                 'size_scale': 1.05,
                 'primary': '&H008AF0FE',
                 'secondary': '&H004444EF',
                 'outline_c': '&H00000000',
                 'back_c': '&H00000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 3,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper'},
 'warm_pastel': {'label': 'Warm Pastel',
                 'category': 'Basic',
                 'desc': 'Lavender text, coral word — wellness/beauty',
                 'font': 'Poppins',
                 'size_scale': 1.0,
                 'primary': '&H00FFE8F3',
                 'secondary': '&H008571FB',
                 'outline_c': '&H80000000',
                 'back_c': '&H80000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 1,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop_soft', 'entrance': 'fadein',
                 'text_case': 'mixed'},
 'creator_pop': {'label': 'Creator Pop',
                 'category': 'Basic',
                 'desc': 'White text, hot-pink word — vlogs/lifestyle',
                 'font': 'Poppins',
                 'size_scale': 1.0,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H009948EC',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': -1,
                 'italic': 0,
                 'outline': 2,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'bounce', 'entrance': 'slideup',
                 'text_case': 'mixed'},
 'tiktok_hype': {'label': 'TikTok Hype',
                 'category': 'Basic',
                 'desc': 'White text, bright orange word — viral reactions',
                 'font': 'Montserrat',
                 'size_scale': 1.05,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H001673F9',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': -1,
                 'italic': 0,
                 'outline': 3,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'upper'},
 'sports_hype': {'label': 'Sports Hype',
                 'category': 'Basic',
                 'desc': 'Condensed caps, yellow word — sports energy',
                 'font': 'Anton',
                 'size_scale': 1.1,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H0000D7FF',
                 'outline_c': '&H00000000',
                 'back_c': '&H00000000',
                 'bold': 0,
                 'italic': 0,
                 'outline': 4,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'shake', 'entrance': 'slideup',
                 'text_case': 'upper'},
 'dark_documentary': {'label': 'Dark Documentary',
                      'category': 'Basic',
                      'desc': 'Pale gray text, crimson word — crime/mystery',
                      'font': 'Poppins',
                      'size_scale': 0.95,
                      'primary': '&H00F9F5F1',
                      'secondary': '&H00481DE1',
                      'outline_c': '&H80000000',
                      'back_c': '&H80000000',
                      'bold': 0,
                      'italic': 0,
                      'outline': 1,
                      'shadow': 1,
                      'alignment': 2,
                      'box': False,
                      'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                      'text_case': 'mixed'},
 'desi_dhamaka': {'label': 'Desi Dhamaka',
                  'category': 'Basic',
                  'desc': 'Bold condensed caps, yellow word — South Asian creator style',
                  'font': 'Anton',
                  'size_scale': 1.05,
                  'primary': '&H00FFFFFF',
                  'secondary': '&H0000D7FF',
                  'outline_c': '&H00000000',
                  'back_c': '&H00000000',
                  'bold': 0,
                  'italic': 0,
                  'outline': 4,
                  'shadow': 1,
                  'alignment': 2,
                  'box': False,
                  'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                  'text_case': 'upper'},
 'red_text': {'label': 'Red Text',
              'category': 'Basic',
              'desc': 'Bold red caps, dark outline — high alert',
              'font': 'Anton',
              'size_scale': 1.05,
              'primary': '&H000000FF',
              'secondary': '&H0000D7FF',
              'outline_c': '&H00000000',
              'back_c': '&H00000000',
              'bold': 0,
              'italic': 0,
              'outline': 3,
              'shadow': 1,
              'alignment': 2,
              'box': False,
              'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
              'text_case': 'upper'},
 'green_highlight': {'label': 'Green Highlight',
                     'category': 'Basic',
                     'desc': 'White caps, thin outline, green spoken word',
                     'font': 'Montserrat',
                     'size_scale': 0.9,
                     'primary': '&H00FFFFFF',
                     'secondary': '&H0076E600',
                     'outline_c': '&H90000000',
                     'back_c': '&H90000000',
                     'bold': -1,
                     'italic': 0,
                     'outline': 2,
                     'shadow': 1,
                     'alignment': 2,
                     'box': False,
                     'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                     'text_case': 'upper'},
 'lime_highlight': {'label': 'Lime Highlight',
                    'category': 'Basic',
                    'desc': 'White caps, black outline, lime spoken word',
                    'font': 'Montserrat',
                    'size_scale': 1.0,
                    'primary': '&H00FFFFFF',
                    'secondary': '&H0035E6A3',
                    'outline_c': '&H90000000',
                    'back_c': '&H90000000',
                    'bold': -1,
                    'italic': 0,
                    'outline': 3,
                    'shadow': 1,
                    'alignment': 2,
                    'box': False,
                    'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                    'text_case': 'upper'},
 'yellow_highlight': {'label': 'Yellow Highlight',
                      'category': 'Basic',
                      'desc': 'White caps, black outline, yellow spoken word',
                      'font': 'Montserrat',
                      'size_scale': 1.0,
                      'primary': '&H00FFFFFF',
                      'secondary': '&H0000D7FF',
                      'outline_c': '&H90000000',
                      'back_c': '&H90000000',
                      'bold': -1,
                      'italic': 0,
                      'outline': 3,
                      'shadow': 1,
                      'alignment': 2,
                      'box': False,
                      'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                      'text_case': 'upper'},
 'plain_bold': {'label': 'Plain Bold',
                'category': 'Basic',
                'desc': 'Simple bold white caps, black outline',
                'font': 'Montserrat',
                'size_scale': 1.0,
                'primary': '&H00FFFFFF',
                'secondary': '&H00FFFFFF',
                'outline_c': '&H90000000',
                'back_c': '&H90000000',
                'bold': -1,
                'italic': 0,
                'outline': 3,
                'shadow': 1,
                'alignment': 2,
                'box': False,
                'highlight_mode': 'karaoke',
                      'anim': 'none', 'entrance': 'fadein',
                'text_case': 'upper'},
 'handwritten': {'label': 'Handwritten',
                 'category': 'Basic',
                 'desc': 'Marker handwriting, gold accent — personal vlogs',
                 'font': 'Caveat',
                 'size_scale': 1.25,
                 'primary': '&H00FFFFFF',
                 'secondary': '&H0000D7FF',
                 'outline_c': '&H90000000',
                 'back_c': '&H90000000',
                 'bold': -1,
                 'italic': 0,
                 'outline': 2,
                 'shadow': 1,
                 'alignment': 2,
                 'box': False,
                 'highlight_mode': 'word',
                      'anim': 'pop', 'entrance': 'slideup',
                 'text_case': 'mixed'},
 # --- Genspark/CapCut full-structure pack (2026-10-01) ---
 # 1. Main Caption Types & Layout Styles (8 structural)
 'word_sync': {'label': 'Word Sync',
               'category': 'Word',
               'desc': 'Word-by-Word Sync: one word at a time, synced to speech — viral shorts',
               'font': 'Anton', 'size_scale': 1.3,
               'primary': '&H00FFFFFF', 'secondary': '&H0000D7FF',
               'outline_c': '&H00000000', 'back_c': '&H00000000',
               'bold': 0, 'italic': 0, 'outline': 4, 'shadow': 1,
               'alignment': 5, 'box': False,
               'highlight_mode': 'singleword',
               'anim': 'pop', 'entrance': 'fadein', 'text_case': 'upper'},
 'highlight_pro': {'label': 'Highlight Pro',
                   'category': 'Highlight',
                   'desc': 'Highlight Captions: full line stays, spoken word changes color + pops',
                   'font': 'Montserrat', 'size_scale': 1.0,
                   'primary': '&H00FFFFFF', 'secondary': '&H0000D7FF',
                   'outline_c': '&H90000000', 'back_c': '&H90000000',
                   'bold': -1, 'italic': 0, 'outline': 3, 'shadow': 1,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'word',
                   'anim': 'pop', 'entrance': 'slideup', 'text_case': 'upper'},
 'uppercase_impact': {'label': 'Uppercase Impact',
                      'category': 'Hits',
                      'desc': 'Uppercase Impact: auto ALL CAPS, gym/motivational punch',
                      'font': 'Anton', 'size_scale': 1.15,
                      'primary': '&H00FFFFFF', 'secondary': '&H0000FFFF',
                      'outline_c': '&H00000000', 'back_c': '&H00000000',
                      'bold': 0, 'italic': 0, 'outline': 4, 'shadow': 2,
                      'alignment': 2, 'box': False,
                      'highlight_mode': 'wordbig',
                      'anim': 'negrow', 'entrance': 'slideup', 'text_case': 'upper'},
 'aesthetic_soft': {'label': 'Aesthetic Soft',
                    'category': 'Aesthetic',
                    'desc': 'Aesthetic Captions: minimalist thin font, soft pastel — lifestyle/vlogs',
                    'font': 'Outfit', 'size_scale': 0.85,
                    'primary': '&H00F5F0EB', 'secondary': '&H00E8B4D0',
                    'outline_c': '&H80000000', 'back_c': '&H80000000',
                    'bold': 0, 'italic': 0, 'outline': 1, 'shadow': 0,
                    'alignment': 2, 'box': False,
                    'highlight_mode': 'word',
                    'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'lower'},
 'minimal_clean': {'label': 'Minimal Clean',
                   'category': 'Basic',
                   'desc': 'Minimal Captions: low-contrast simple subtitles, information only',
                   'font': 'Outfit', 'size_scale': 0.75,
                   'primary': '&H00D8D8D8', 'secondary': '&H00FFFFFF',
                   'outline_c': '&H60000000', 'back_c': '&H60000000',
                   'bold': 0, 'italic': 0, 'outline': 1, 'shadow': 0,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'word',
                   'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'mixed'},
 'dynamic_rise': {'label': 'Dynamic Rise',
                  'category': 'NEW',
                  'desc': 'Dynamic Animated: captions rise from bottom with bounce — attention grab',
                  'font': 'Poppins', 'size_scale': 1.05,
                  'primary': '&H00FFFFFF', 'secondary': '&H0000FF00',
                  'outline_c': '&H90000000', 'back_c': '&H90000000',
                  'bold': -1, 'italic': 0, 'outline': 3, 'shadow': 1,
                  'alignment': 2, 'box': False,
                  'highlight_mode': 'word',
                  'anim': 'bounce', 'entrance': 'slideup', 'text_case': 'upper'},
 'standard_subs': {'label': 'Standard Subs',
                   'category': 'Classic',
                   'desc': 'Standard Subtitles: plain two-line movie subtitles, no animation',
                   'font': 'Arial', 'size_scale': 0.8,
                   'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
                   'outline_c': '&HFF000000', 'back_c': '&HFF000000',
                   'bold': 0, 'italic': 0, 'outline': 2, 'shadow': 1,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'standard',
                   'anim': 'none', 'entrance': 'none', 'text_case': 'mixed'},
 'dual_language': {'label': 'Dual Language',
                   'category': 'Multiline',
                   'desc': 'Dual-Language: English + second line together (Urdu/translation)',
                   'font': 'Poppins', 'size_scale': 0.9,
                   'primary': '&H00FFFFFF', 'secondary': '&H0000D7FF',
                   'outline_c': '&H90000000', 'back_c': '&H90000000',
                   'bold': -1, 'italic': 0, 'outline': 2, 'shadow': 1,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'dual',
                   'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'mixed'},
 # 2. Built-in Preset Styles (visual looks)
 'classic_plain': {'label': 'Classic Plain',
                   'category': 'Classic',
                   'desc': 'Classic: white text, light black outline',
                   'font': 'Arial', 'size_scale': 0.9,
                   'primary': '&H00FFFFFF', 'secondary': '&H00FFFF00',
                   'outline_c': '&H80000000', 'back_c': '&H80000000',
                   'bold': -1, 'italic': 0, 'outline': 2, 'shadow': 1,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'word',
                   'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'mixed'},
 'glow_neon_pro': {'label': 'Neon Glow Pro',
                   'category': 'Glow',
                   'desc': 'Glow: neon light glow around text',
                   'font': 'Bebas Neue', 'size_scale': 1.1,
                   'primary': '&H00FFFFFF', 'secondary': '&H00FF00FF',
                   'outline_c': '&H80000000', 'back_c': '&H80000000',
                   'glow_color': '&H00FF00FF',
                   'bold': 0, 'italic': 0, 'outline': 2, 'shadow': 0,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'wordglow',
                   'anim': 'pop', 'entrance': 'fadein', 'text_case': 'upper'},
 'monoline_thin': {'label': 'Monoline Thin',
                   'category': 'Monoline',
                   'desc': 'Monoline: thin outline only, no fill — vlog style',
                   'font': 'Bebas Neue', 'size_scale': 1.1,
                   'primary': '&H00000000', 'secondary': '&H0000D7FF',
                   'outline_c': '&H00FFFFFF', 'back_c': '&H00000000',
                   'bold': 0, 'italic': 0, 'outline': 2, 'shadow': 0,
                   'alignment': 2, 'box': False,
                   'highlight_mode': 'word',
                   'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'upper'},
 'textbox_black': {'label': 'Textbox Black',
                   'category': 'Boxed',
                   'desc': 'Text Box: solid black box behind text, readable on any background',
                   'font': 'Montserrat', 'size_scale': 0.95,
                   'primary': '&H00FFFFFF', 'secondary': '&H0000FFFF',
                   'outline_c': '&HFF000000', 'back_c': '&HFF000000',
                   'bold': -1, 'italic': 0, 'outline': 0, 'shadow': 0,
                   'alignment': 2, 'box': True,
                   'highlight_mode': 'word',
                   'anim': 'pop', 'entrance': 'slideup', 'text_case': 'upper'},
 'textbox_yellow': {'label': 'Textbox Yellow',
                    'category': 'Boxed',
                    'desc': 'Text Box: solid yellow box, black text',
                    'font': 'Montserrat', 'size_scale': 0.95,
                    'primary': '&H00000000', 'secondary': '&H000000FF',
                    'outline_c': '&H0000FFFF', 'back_c': '&H0000FFFF',
                    'bold': -1, 'italic': 0, 'outline': 0, 'shadow': 0,
                    'alignment': 2, 'box': True,
                    'highlight_mode': 'word',
                    'anim': 'pop', 'entrance': 'slideup', 'text_case': 'upper'},
 'textbox_red': {'label': 'Textbox Red',
                 'category': 'Boxed',
                 'desc': 'Text Box: solid red box, white text — breaking news',
                 'font': 'Anton', 'size_scale': 1.0,
                 'primary': '&H00FFFFFF', 'secondary': '&H00000000',
                 'outline_c': '&H000000FF', 'back_c': '&H000000FF',
                 'bold': 0, 'italic': 0, 'outline': 0, 'shadow': 0,
                 'alignment': 2, 'box': True,
                 'highlight_mode': 'word',
                 'anim': 'pop', 'entrance': 'slideup', 'text_case': 'upper'},
 'trending_yellow_white': {'label': 'Trending YW',
                           'category': 'Trending',
                           'desc': 'Trending Combo: white words, keywords auto-yellow — most famous preset',
                           'font': 'Montserrat', 'size_scale': 1.0,
                           'primary': '&H00FFFFFF', 'secondary': '&H0000FFFF',
                           'outline_c': '&H90000000', 'back_c': '&H90000000',
                           'bold': -1, 'italic': 0, 'outline': 3, 'shadow': 1,
                           'alignment': 2, 'box': False,
                           'highlight_mode': 'word',
                           'anim': 'pop', 'entrance': 'slideup', 'text_case': 'mixed'},
 # 3. Viral Caption Animations (motion styles)
 'anim_single_bounce': {'label': 'Single Bounce',
                        'category': 'NEW',
                        'desc': 'Single Line Bounce: word pops bigger then bounces on arrival',
                        'font': 'Anton', 'size_scale': 1.1,
                        'primary': '&H00FFFFFF', 'secondary': '&H0000D7FF',
                        'outline_c': '&H00000000', 'back_c': '&H00000000',
                        'bold': 0, 'italic': 0, 'outline': 4, 'shadow': 1,
                        'alignment': 2, 'box': False,
                        'highlight_mode': 'wordbig',
                        'anim': 'bounce', 'entrance': 'slideup', 'text_case': 'upper'},
 'anim_negative_grow': {'label': 'Negative Grow',
                        'category': 'NEW',
                        'desc': 'Negative Grow: spoken word grows big mid-sentence',
                        'font': 'Anton', 'size_scale': 1.0,
                        'primary': '&H00FFFFFF', 'secondary': '&H0000FFFF',
                        'outline_c': '&H00000000', 'back_c': '&H00000000',
                        'bold': 0, 'italic': 0, 'outline': 4, 'shadow': 1,
                        'alignment': 2, 'box': False,
                        'highlight_mode': 'wordbig',
                        'anim': 'negrow', 'entrance': 'slideup', 'text_case': 'upper'},
 'anim_flip_3d': {'label': 'Flip 3D',
                  'category': 'NEW',
                  'desc': 'Flip 6 (3D): text flips in with 3D perspective rotation',
                  'font': 'Bebas Neue', 'size_scale': 1.15,
                  'primary': '&H00FFFFFF', 'secondary': '&H00FF00FF',
                  'outline_c': '&H90000000', 'back_c': '&H90000000',
                  'bold': 0, 'italic': 0, 'outline': 3, 'shadow': 1,
                  'alignment': 2, 'box': False,
                  'highlight_mode': 'word',
                  'anim': 'flip', 'entrance': 'fadein', 'text_case': 'upper'},
 'anim_typewriter_pro': {'label': 'Typewriter Pro',
                         'category': 'Word',
                         'desc': 'Typewriter: captions type out live character by character',
                         'font': 'Courier Prime', 'size_scale': 0.95,
                         'primary': '&H0000FF00', 'secondary': '&H0000FF00',
                         'outline_c': '&HFF000000', 'back_c': '&HFF000000',
                         'bold': -1, 'italic': 0, 'outline': 1, 'shadow': 0,
                         'alignment': 2, 'box': False,
                         'highlight_mode': 'typewriter',
                         'anim': 'none', 'entrance': 'none', 'text_case': 'mixed'},
 'anim_karaoke_smooth': {'label': 'Karaoke Smooth',
                         'category': 'Word',
                         'desc': 'Karaoke: color sweeps left-to-right like song lyrics',
                         'font': 'Poppins', 'size_scale': 1.0,
                         'primary': '&H00FFFFFF', 'secondary': '&H0000D7FF',
                         'outline_c': '&H90000000', 'back_c': '&H90000000',
                         'bold': -1, 'italic': 0, 'outline': 3, 'shadow': 1,
                         'alignment': 2, 'box': False,
                         'highlight_mode': 'karaoke', 'karaoke_smooth': True,
                         'anim': 'none', 'entrance': 'fadein', 'text_case': 'mixed'},
 # extra variety: B&W, Hits, Multiline
 'bw_bold': {'label': 'B&W Bold',
             'category': 'B&W',
             'desc': 'Black & white bold condensed — newspaper punch',
             'font': 'Anton', 'size_scale': 1.1,
             'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
             'outline_c': '&HFF000000', 'back_c': '&HFF000000',
             'bold': 0, 'italic': 0, 'outline': 3, 'shadow': 0,
             'alignment': 2, 'box': False,
             'highlight_mode': 'wordbox',
             'box_color': '&HFF000000',
             'anim': 'pop', 'entrance': 'slideup', 'text_case': 'upper'},
 'bw_invert': {'label': 'B&W Invert',
               'category': 'B&W',
               'desc': 'Inverted: black text on white box',
               'font': 'Montserrat', 'size_scale': 1.0,
               'primary': '&H00000000', 'secondary': '&H00000000',
               'outline_c': '&H00FFFFFF', 'back_c': '&H00FFFFFF',
               'bold': -1, 'italic': 0, 'outline': 0, 'shadow': 0,
               'alignment': 2, 'box': True,
               'highlight_mode': 'word',
               'anim': 'pop_soft', 'entrance': 'fadein', 'text_case': 'upper'},
 'hits_fire': {'label': 'Hits Fire',
               'category': 'Hits',
               'desc': 'Fire hits style: orange glow hype captions',
               'font': 'Bangers', 'size_scale': 1.2,
               'primary': '&H00FFFFFF', 'secondary': '&H000080FF',
               'outline_c': '&H90000000', 'back_c': '&H90000000',
               'glow_color': '&H000080FF',
               'bold': 0, 'italic': 0, 'outline': 3, 'shadow': 1,
               'alignment': 2, 'box': False,
               'highlight_mode': 'wordglow',
               'anim': 'bounce', 'entrance': 'slideup', 'text_case': 'upper'},
 'multiline_stack': {'label': 'Multiline Stack',
                     'category': 'Multiline',
                     'desc': 'Stacked two-line captions, centered',
                     'font': 'Outfit', 'size_scale': 0.9,
                     'primary': '&H00FFFFFF', 'secondary': '&H0000FFFF',
                     'outline_c': '&H90000000', 'back_c': '&H90000000',
                     'bold': -1, 'italic': 0, 'outline': 2, 'shadow': 1,
                     'alignment': 5, 'box': False,
                     'highlight_mode': 'word',
                     'anim': 'pop_soft', 'entrance': 'slideup', 'text_case': 'mixed'},
}

CAPTION_TEMPLATE_CATEGORIES = ['Trending', 'Classic', 'NEW', 'Hits', 'Word', 'Glow', 'Basic', 'Aesthetic', 'Monoline', 'Multiline', 'Highlight', 'B&W', 'Boxed']

DEFAULT_CAPTION_TEMPLATE = "tiktok_classic"


def caption_template_labels():
    """Ordered (key, label) pairs for UI dropdowns."""
    return [(k, v["label"]) for k, v in CAPTION_TEMPLATES.items()]


def make_overlay_text(analysis, max_words=5):
    """Short punchy overlay, never the whole sentence."""
    parts = []
    for p in analysis.phrases or []:
        for w in p.split():
            if w.lower() not in [x.lower() for x in parts]:
                parts.append(w)
    for k in analysis.keywords or []:
        if k.lower() not in [x.lower() for x in parts]:
            parts.append(k)
    return " ".join(parts[:max_words]).upper()


def _ts_srt(sec):
    sec = max(0.0, sec)
    h, rem = int(sec // 3600), sec % 3600
    m, rem = int(rem // 60), rem % 60
    return f"{h:02d}:{m:02d}:{int(rem):02d},{int(rem % 1 * 1000):03d}"


def _ts_ass(sec):
    sec = max(0.0, sec)
    h, rem = int(sec // 3600), sec % 3600
    m, rem = int(rem // 60), rem % 60
    return f"{h}:{m:02d}:{int(rem):02d}.{int(rem % 1 * 100):02d}"


# ---------------------------------------------------------------------------
# Kinetic typography animation engines (CapCut-style animated captions).
#
# Tag mechanics verified against libass master ass_parse.c (2026-09-30):
#  * \t(t1,t2[,accel],tags): t1/t2 = ms from EVENT start (not absolute);
#    k=pow((t-t1)/(t2-t1),accel) clamped to [0,1]; sequential \t tags chain
#    correctly; nested \t is NOT supported (inner overwrites outer).
#  * Transformable in \t: \fscx \fscy \fs \frz \bord \shad \1c-\4c \alpha...
#    NOT transformable: \pos \move \fad \fade \k* \an (first-wins / own timing).
#  * Word animations use \t on \fscx/\fscy/\1c/\frz (cheap affine+color ops).
#    Entrance animations use \fad and \move ONLY (no tag overlap -> no
#    conflicts between entrance and word-level transforms).
# ---------------------------------------------------------------------------

def _word_anim_open(anim, sec, pri, tmpl):
    """ASS override block for the ACTIVE (spoken) word.

    Returns (open_tags, close_tags). open_tags goes before the word,
    close_tags after it. Times in \t are ms relative to the word-event's
    own start, so each word pops exactly when it is spoken.
    """
    c_open = "{\\c%s" % sec
    c_close = "{\\c%s}" % pri
    if anim == "pop":
        # 100 -> 135% over 150ms, settle back by 350ms (CapCut signature)
        return (c_open + "\\t(0,150,\\fscx135\\fscy135)"
                "\\t(150,350,\\fscx100\\fscy100)}", c_close)
    if anim == "pop_soft":
        # gentler 118% pop for minimal/clean styles
        return (c_open + "\\t(0,150,\\fscx118\\fscy118)"
                "\\t(150,350,\\fscx100\\fscy100)}", c_close)
    if anim == "bounce":
        # pop -> slight undershoot -> settle (k clamps to [0,1], so bounce
        # needs chained \t; accel 0.6 = fast start, ease out)
        return (c_open + "\\t(0,120,0.6,\\fscx140\\fscy140)"
                "\\t(120,220,\\fscx95\\fscy95)"
                "\\t(220,340,\\fscx100\\fscy100)}", c_close)
    if anim == "shake":
        # rotational wobble via \frz (chained \move does NOT work in libass)
        return (c_open + "\\t(0,60,\\frz-3)\\t(60,120,\\frz3)"
                "\\t(120,180,\\frz0)}", c_close)
    if anim == "flip":
        # Flip 6 (3D): rotate around Y axis from -90 to 0 (CapCut 3D flip)
        # \fry is transformable in \t
        return (c_open + "\\fry-90\\t(0,250,\\fry0)}", c_close)
    if anim == "negrow":
        # Negative Grow: spoken word grows much bigger and stays big
        return (c_open + "\\t(0,180,0.6,\\fscx165\\fscy165)"
                "\\t(180,320,\\fscx150\\fscy150)}", c_close)
    if anim == "bounce_single":
        # Single Line Bounce: whole line bounces on entrance
        return (c_open + "\\t(0,150,0.5,\\fscx130\\fscy130)"
                "\\t(150,300,\\fscx100\\fscy100)}", c_close)
    # "none" or unknown: static color swap (old behaviour)
    return (c_open + "}", c_close)


def _wordbox_anim_open(anim, tmpl, sec, pri):
    """Active-word tags for the wordbox style (colored box + optional pop)."""
    box_c = tmpl.get("box_color", "&H00FF0000")
    bo = tmpl.get("outline", 2)
    oc = tmpl["outline_c"]
    base_open = "{\\bord6\\3c%s\\c%s" % (box_c, sec)
    base_close = "{\\bord%d\\3c%s\\c%s}" % (bo, oc, pri)
    if anim == "pop":
        return (base_open + "\\t(0,150,\\fscx130\\fscy130)"
                "\\t(150,350,\\fscx100\\fscy100)}", base_close)
    if anim == "bounce":
        return (base_open + "\\t(0,120,0.6,\\fscx140\\fscy140)"
                "\\t(120,220,\\fscx95\\fscy95)"
                "\\t(220,340,\\fscx100\\fscy100)}", base_close)
    if anim == "shake":
        return (base_open + "\\t(0,60,\\frz-3)\\t(60,120,\\frz3)"
                "\\t(120,180,\\frz0)}", base_close)
    if anim == "flip":
        return (base_open + "\\fry-90\\t(0,250,\\fry0)}", base_close)
    if anim == "negrow":
        return (base_open + "\\t(0,180,0.6,\\fscx160\\fscy160)"
                "\\t(180,320,\\fscx145\\fscy145)}", base_close)
    return (base_open + "}", base_close)


def _wordglow_anim_open(anim, tmpl, sec, pri):
    """Active-word tags for the wordglow style (colored glow + optional pop)."""
    glow_c = tmpl.get("glow_color", sec)
    bo = tmpl.get("outline", 2)
    oc = tmpl["outline_c"]
    base_open = "{\\c%s\\bord4\\3c%s" % (sec, glow_c)
    base_close = "{\\c%s\\bord%d\\3c%s}" % (pri, bo, oc)
    if anim == "pop":
        return (base_open + "\\t(0,150,\\fscx130\\fscy130)"
                "\\t(150,350,\\fscx100\\fscy100)}", base_close)
    if anim == "bounce":
        return (base_open + "\\t(0,120,0.6,\\fscx140\\fscy140)"
                "\\t(120,220,\\fscx95\\fscy95)"
                "\\t(220,340,\\fscx100\\fscy100)}", base_close)
    if anim == "shake":
        return (base_open + "\\t(0,60,\\frz-3)\\t(60,120,\\frz3)"
                "\\t(120,180,\\frz0)}", base_close)
    if anim == "flip":
        return (base_open + "\\fry-90\\t(0,250,\\fry0)}", base_close)
    if anim == "negrow":
        return (base_open + "\\t(0,180,0.6,\\fscx160\\fscy160)"
                "\\t(180,320,\\fscx145\\fscy145)}", base_close)
    return (base_open + "}", base_close)


def _wordbig_anim_open(anim, tmpl, sec, pri):
    """Active-word tags for wordbig: bigger + colored, with pop animation."""
    if anim == "pop":
        # punch to 150%, settle at 120% (stays emphasized), reset after
        return ("{\\c%s\\t(0,150,\\fscx150\\fscy150)"
                "\\t(150,350,\\fscx120\\fscy120)}" % sec,
                "{\\c%s\\fscx100\\fscy100}" % pri)
    if anim == "bounce":
        return ("{\\c%s\\t(0,120,0.6,\\fscx160\\fscy160)"
                "\\t(120,220,\\fscx110\\fscy110)"
                "\\t(220,340,\\fscx120\\fscy120)}" % sec,
                "{\\c%s\\fscx100\\fscy100}" % pri)
    if anim == "shake":
        return ("{\\c%s\\fscx120\\fscy120"
                "\\t(0,60,\\frz-3)\\t(60,120,\\frz3)\\t(120,180,\\frz0)}" % sec,
                "{\\c%s\\fscx100\\fscy100}" % pri)
    if anim == "flip":
        return ("{\\c%s\\fscx120\\fscy120\\fry-90\\t(0,250,\\fry0)}" % sec,
                "{\\c%s\\fscx100\\fscy100}" % pri)
    if anim in ("negrow", "bounce_single"):
        return ("{\\c%s\\t(0,180,0.6,\\fscx175\\fscy175)"
                "\\t(180,320,\\fscx150\\fscy150)}" % sec,
                "{\\c%s\\fscx100\\fscy100}" % pri)
    # static fallback (old behaviour): instant \fs size jump
    cap_size = tmpl.get("_cap_size", 48)
    big_size = int(cap_size * tmpl.get("big_scale", 1.8))
    return ("{\\fs%d\\c%s}" % (big_size, sec),
            "{\\fs%d\\c%s}" % (cap_size, pri))


def _entrance_tags(entrance, alignment, pw, ph):
    """Line-entrance animation tags (prepended to the event text).

    Uses ONLY \\fad and \\move so it never conflicts with the word-level
    \\t(\\fscx/\\fscy/\\1c) transforms.
    """
    if entrance == "fadein":
        return "{\\fad(180,0)}"
    if entrance == "slideup":
        # resting anchor depends on alignment; \\move positions the anchor.
        # \\an2 (bottom-center): anchor = (pw/2, ph - MarginV); MarginV = 60.
        # \\an5 (middle-center): anchor = (pw/2, ph/2).
        # \\an8 (top-center):    anchor = (pw/2, MarginV).
        if alignment == 8:
            x, y = pw // 2, 60
        elif alignment == 5:
            x, y = pw // 2, ph // 2
        else:
            x, y = pw // 2, ph - 60
        return "{\\move(%d,%d,%d,%d,0,250)\\fad(250,0)}" % (x, y + 50, x, y)
    return ""


def build_srt(sentence_timings, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sentence_timings, 1):
            f.write(f"{i}\n{_ts_srt(s['start'])} --> {_ts_srt(s['end'])}\n{s['text']}\n\n")
    return path


def build_ass(sentence_timings, word_timings, path, font_size=48, highlight=True,
              overlays=None, play_res=(1920, 1080), template=None):
    """Build the ASS file: bottom captions + optional top overlays.

    overlays: list of dicts {text, start, end}. They use the "Top" style
    (alignment 8 = top-center) so no drawtext filter is ever needed.
    template: key from CAPTION_TEMPLATES (CapCut-style caption look).
    """
    tmpl = CAPTION_TEMPLATES.get(template or DEFAULT_CAPTION_TEMPLATE,
                                 CAPTION_TEMPLATES[DEFAULT_CAPTION_TEMPLATE])
    pw, ph = play_res
    cap_size = max(24, int(ph * font_size / 1080 * tmpl["size_scale"]))
    top_size = max(30, int(ph / 20))
    # BorderStyle: 3 = opaque box (Hormozi), 4 = rounded box (pill),
    # else 1 = outline.
    if tmpl.get("pill"):
        border_style = 4
    elif tmpl["box"]:
        border_style = 3
    else:
        border_style = 1
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,%s,%d,%s,%s,%s,%s,%d,%d,0,0,100,100,0,0,%d,%d,%d,%d,40,40,60,1
Style: Top,Arial,%d,&H00FFFFFF,&H000019FF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,60,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (pw, ph, tmpl["font"], cap_size, tmpl["primary"], tmpl["secondary"],
       tmpl["outline_c"], tmpl["back_c"], tmpl["bold"], tmpl["italic"],
       border_style, tmpl["outline"], tmpl["shadow"], tmpl["alignment"],
       top_size)
    lines = []
    hmode = tmpl.get("highlight_mode", "karaoke")
    anim = tmpl.get("anim", "pop")
    entrance = tmpl.get("entrance", "slideup")
    tcase = tmpl.get("text_case")
    tmpl["_cap_size"] = cap_size  # for wordbig static fallback sizing

    def _case(w: str) -> str:
        if tcase == "upper":
            return w.upper()
        if tcase == "lower":
            return w.lower()
        return w

    if highlight and word_timings and hmode in ("word", "wordbox", "wordbig", "wordglow"):
        # CapCut "Word Highlight": the FULL sentence stays on screen, but
        # only the CURRENT word is emphasized (non-cumulative). One event per
        # word, each spanning until the next word starts.
        #   word:    current word colored + kinetic pop/bounce/shake
        #   wordbox: current word on a colored box + kinetic pop
        #   wordbig: current word bigger + colored, animated scale punch
        #   wordglow: current word with colored glow outline + kinetic pop
        # The active word gets \\t(\\fscx/\\fscy) transforms timed from the
        # word-event's own start, so each word pops exactly when spoken.
        sec = tmpl["secondary"]
        pri = tmpl["primary"]
        words = list(word_timings)
        wi = 0
        for s in sentence_timings:
            seg = []
            while wi < len(words) and words[wi]["end"] <= s["end"] + 0.05:
                seg.append(words[wi])
                wi += 1
            if not seg:
                w0 = _case(s["text"])
                safe = sanitize_ass_text(w0)
                ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph)
                lines.append(
                    f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                    f"Cap,,0,0,0,,{ent}{safe}")
                continue
            sent_words = [_case(w["word"]) for w in seg]
            for i, w in enumerate(seg):
                ws, we = w["start"], w["end"]
                if i + 1 < len(seg):
                    we = seg[i + 1]["start"]
                else:
                    we = max(we, s["end"])
                if we <= ws:
                    we = ws + 0.01
                # entrance animation only on the first word-event of the line
                ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph) if i == 0 else ""
                if hmode == "wordbox":
                    wopen, wclose = _wordbox_anim_open(anim, tmpl, sec, pri)
                elif hmode == "wordbig":
                    wopen, wclose = _wordbig_anim_open(anim, tmpl, sec, pri)
                elif hmode == "wordglow":
                    wopen, wclose = _wordglow_anim_open(anim, tmpl, sec, pri)
                else:
                    wopen, wclose = _word_anim_open(anim, sec, pri, tmpl)
                parts = []
                for j, sw_ in enumerate(sent_words):
                    safe = sanitize_ass_text(sw_)
                    if j == i:
                        parts.append("%s%s%s" % (wopen, safe, wclose))
                    else:
                        parts.append(safe)
                txt = " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},"
                    f"Cap,,0,0,0,,{ent}{txt}")
    elif highlight and word_timings and hmode == "typewriter":
        # Real typewriter: one event per character step, progressively
        # revealing the sentence (centered growth looks best for captions).
        for s in sentence_timings:
            text = _case(s["text"])
            n = len(text)
            if n == 0:
                continue
            dur = max(0.3, s["end"] - s["start"])
            step = dur / n
            for i in range(1, n + 1):
                cs = s["start"] + (i - 1) * step
                ce = s["start"] + i * step if i < n else s["end"]
                safe = sanitize_ass_text(text[:i])
                lines.append(
                    f"Dialogue: 0,{_ts_ass(cs)},{_ts_ass(ce)},"
                    f"Cap,,0,0,0,,{safe}")
    elif highlight and word_timings and hmode == "singleword":
        # Word-by-Word Sync (CapCut viral shorts): only ONE word on screen
        # at a time, synced to speech, with kinetic pop.
        sec = tmpl["secondary"]
        pri = tmpl["primary"]
        for w in word_timings:
            ws, we = w["start"], w["end"]
            if we <= ws:
                we = ws + 0.2
            wopen, wclose = _word_anim_open(anim, sec, pri, tmpl)
            ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph)
            safe = sanitize_ass_text(_case(w["word"]))
            lines.append(
                f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},"
                f"Cap,,0,0,0,,{ent}{wopen}{safe}{wclose}")
    elif highlight and word_timings and hmode == "dual":
        # Dual-Language Captions: original line + second line below.
        # Second line reuses the sentence text (user's translation/Urdu can
        # replace it); rendered smaller via \fs.
        dual_size = max(18, int(cap_size * 0.62))
        for s in sentence_timings:
            safe = sanitize_ass_text(_case(s["text"]))
            ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph)
            # main line
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{ent}{safe}")
            # second language line (smaller, secondary color)
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{{\\fs{dual_size}\\c{tmpl['secondary']}}}"
                f"{safe}{{\\c{tmpl['primary']}}}")
    elif highlight and word_timings and hmode == "standard":
        # Standard Subtitles: plain two-line text, no highlight, no anim.
        for s in sentence_timings:
            safe = sanitize_ass_text(_case(s["text"]))
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{safe}")
    elif highlight and word_timings:
        # karaoke-style per-word highlight
        k_tag = "\\kf" if tmpl.get("karaoke_smooth") else "\\k"
        ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph)
        words = list(word_timings)
        wi = 0
        for s in sentence_timings:
            seg = []
            while wi < len(words) and words[wi]["end"] <= s["end"] + 0.05:
                seg.append(words[wi])
                wi += 1
            if not seg:
                seg = [{"word": s["text"], "start": s["start"], "end": s["end"]}]
            txt = "".join("{%s%d}%s " % (k_tag, max(1, int((w["end"] - w["start"]) * 100)),
                                          sanitize_ass_text(_case(w["word"])))
                          for w in seg)
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{ent}{txt.strip()}")
    else:
        for s in sentence_timings:
            safe = sanitize_ass_text(s["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{safe}")
    for ov in overlays or []:
        txt = sanitize_ass_text(ov["text"])
        if not txt.strip():
            continue
        lines.append(f"Dialogue: 1,{_ts_ass(ov['start'])},{_ts_ass(ov['end'])},Top,,0,0,0,,{txt}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return path


def youtube_suggest(analysis, scenes, timings):
    """Titles, description, chapters, tags, thumbnail text from the real script data."""
    kws = []
    for s in analysis.sentences:
        kws += s.keywords
    from collections import Counter
    top = [w for w, _ in Counter(kws).most_common(6)]
    topic = analysis.topic.replace("_", " ").title()
    base = " ".join(w.capitalize() for w in top[:4])
    titles = [
        f"{base} Explained: What You Need to Know",
        f"The Truth About {topic} ({base})",
        f"{base} | {topic} Documentary",
    ]
    desc = (f"{base}.\n\nIn this video we break down {topic.lower()} step by step.\n\n"
            f"Topics covered: {', '.join(top)}.\n")
    chapters = []
    for sc, t in zip(scenes, timings):
        m, sec = int(t["start"] // 60), int(t["start"] % 60)
        chapters.append(f"{m:02d}:{sec:02d} - {sc.text[:60]}")
    tags = list(dict.fromkeys(top + [analysis.topic, "documentary", "explainer"]))
    thumb = make_overlay_text(analysis.sentences[0], max_words=4) if analysis.sentences else topic.upper()
    return {"titles": titles, "description": desc, "chapters": chapters,
            "tags": tags, "thumbnail_text": thumb}
