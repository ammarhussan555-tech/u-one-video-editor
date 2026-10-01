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
 # --- SPEC ENGINE: CapCut 8 structural + 5 presets + 5 motions (2026-10-01) ---
 # Full CaptionStyle configs per user spec. past/upcoming define the
 # 3-state word machine fills (spec 1.3). Sizes are px @1080p expressed as
 # size_scale relative to the base caption font size (engine multiplies by
 # frameHeight/1080 per spec step 6.4).
 'spec_word_sync': {'label': 'SPEC 1 Word Sync',
                    'category': 'Word',
                    'desc': 'Spec style 1: ONE word at a time, middle screen, popIn 1.08',
                    'font': 'Montserrat', 'size_scale': 2.29,
                    'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
                    'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
                    'outline_c': '&H00000000', 'back_c': '&H00000000',
                    'bold': -1, 'italic': 0, 'outline': 8, 'shadow': 0,
                    'alignment': 5, 'box': False,
                    'highlight_mode': 'singleword',
                    'anim': 'popin', 'entrance': 'none', 'text_case': 'upper'},
 'spec_highlight': {'label': 'SPEC 2 Highlight',
                    'category': 'Highlight',
                    'desc': 'Spec style 2: full line stays, ONLY spoken word turns yellow + 1.12 pop',
                    'font': 'Montserrat', 'size_scale': 1.83,
                    'primary': '&H00FFFFFF', 'secondary': '&H002FF3FE',
                    'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
                    'outline_c': '&H00000000', 'back_c': '&H00000000',
                    'bold': -1, 'italic': 0, 'outline': 6, 'shadow': 1,
                    'alignment': 2, 'margin_v_frac': 0.22, 'box': False,
                    'highlight_mode': 'word',
                    'anim': 'pop12', 'entrance': 'slideup', 'text_case': 'mixed'},
 'spec_impact': {'label': 'SPEC 3 Uppercase Impact',
                 'category': 'Hits',
                 'desc': 'Spec style 3: EVERY word ALL CAPS in its OWN black box, slam entry',
                 'font': 'Anton', 'size_scale': 2.5,
                 'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
                 'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
                 'outline_c': '&H00000000', 'back_c': '&H00000000',
                 'bold': -1, 'italic': 0, 'outline': 10, 'shadow': 0,
                 'alignment': 5, 'margin_v_frac': 0.25, 'box': True,
                 'box_opacity': 0.85, 'box_pad': 14,
                 'highlight_mode': 'wordbox_each',
                 'anim': 'slamin', 'entrance': 'none', 'text_case': 'upper'},
 'spec_aesthetic': {'label': 'SPEC 4 Aesthetic',
                    'category': 'Aesthetic',
                    'desc': 'Spec style 4: soft serif, no outline, no box, gentle fade-up',
                    'font': 'Playfair Display', 'size_scale': 1.33,
                    'primary': '&H00E6EFF5', 'secondary': '&H00E6EFF5',
                    'past': '&H00E6EFF5', 'upcoming': '&H00E6EFF5',
                    'outline_c': '&H00000000', 'back_c': '&H66000000',
                    'bold': 0, 'italic': 0, 'outline': 0, 'shadow': 1,
                    'spacing': 2,
                    'alignment': 2, 'margin_v_frac': 0.15, 'box': False,
                    'highlight_mode': 'word',
                    'anim': 'none', 'entrance': 'fadeup', 'text_case': 'mixed'},
 'spec_minimal': {'label': 'SPEC 5 Minimal',
                  'category': 'Basic',
                  'desc': 'Spec style 5: static-looking, low contrast, only 0.2s fade',
                  'font': 'Inter', 'size_scale': 1.17,
                  'primary': '&H00E0E0E0', 'secondary': '&H00E0E0E0',
                  'past': '&H00E0E0E0', 'upcoming': '&H00E0E0E0',
                  'outline_c': '&H00000000', 'back_c': '&H00000000',
                  'bold': 0, 'italic': 0, 'outline': 2, 'shadow': 0,
                  'alignment': 2, 'margin_v_frac': 0.10, 'box': False,
                  'highlight_mode': 'word',
                  'anim': 'none', 'entrance': 'fade02', 'text_case': 'mixed'},
 'spec_dynamic': {'label': 'SPEC 6 Dynamic Animated',
                  'category': 'Animated',
                  'desc': 'Spec style 6: entry cycles slideUp/bounceIn/slideLeft per chunk',
                  'font': 'Montserrat', 'size_scale': 1.83,
                  'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
                  'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
                  'outline_c': '&H00000000', 'back_c': '&H00000000',
                  'bold': -1, 'italic': 0, 'outline': 6, 'shadow': 0,
                  'alignment': 2, 'margin_v_frac': 0.20, 'box': False,
                  'highlight_mode': 'word',
                  'anim': 'pop106',
                  'entrance_cycle': ['rise300', 'bouncein', 'slideleft'],
                  'text_case': 'mixed'},
 'spec_standard': {'label': 'SPEC 7 Standard Subtitles',
                   'category': 'Basic',
                   'desc': 'Spec style 7: plain bottom subtitles, no animation',
                   'font': 'Open Sans', 'size_scale': 1.0,
                   'primary': '&H00FFFFFF', 'secondary': '&H00FFFFFF',
                   'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
                   'outline_c': '&H00000000', 'back_c': '&H00000000',
                   'bold': 0, 'italic': 0, 'outline': 2, 'shadow': 0,
                   'alignment': 2, 'margin_v_frac': 0.08, 'box': False,
                   'highlight_mode': 'word',
                   'anim': 'none', 'entrance': 'fade01', 'text_case': 'mixed',
                   # app layer: chunk with max 7 words / 2 lines for this style
                   'chunk_max_words': 7},
 'spec_dual': {'label': 'SPEC 8 Dual-Language',
               'category': 'Hits',
               'desc': 'Spec style 8: two independent tracks, primary + genuine translation',
               'font': 'Montserrat', 'size_scale': 1.33,
               'primary': '&H00FFFFFF', 'secondary': '&H0007C1FF',
               'past': '&H00FFFFFF', 'upcoming': '&H00FFFFFF',
               'outline_c': '&H00000000', 'back_c': '&H00000000',
               'bold': -1, 'italic': 0, 'outline': 6, 'shadow': 0,
               'alignment': 2, 'margin_v_frac': 0.22, 'box': False,
               'highlight_mode': 'word',
               'anim': 'pop12', 'entrance': 'fadein', 'text_case': 'mixed',
               'dual': True,
               'secondary_cfg': {'font': 'Noto Sans Arabic', 'size_scale': 0.92,
                                'fill': '&H00D0D0D0', 'outline': 3,
                                'margin_v_frac': 0.12}},
}


# ---------------------------------------------------------------------------
# Spec step: 5 visual presets (overrides, combinable with any structural
# style) + 5 composable motion animations.
# A preset/motion is a dict of CaptionStyle fields that override the
# structural style's. compose_template() merges style <- preset <- motion.
# ---------------------------------------------------------------------------
CAPTION_PRESETS = {
    'preset_classic': {
        'label': 'Classic',
        'desc': 'The structural style exactly as designed (no override)',
    },
    'preset_glow': {
        'label': 'Glow',
        'desc': 'Neon cyan (#00E5FF) glow on the spoken word',
        'highlight_mode': 'wordglow',
        'glow_color': '&H00FFE500',  # #00E5FF in ASS BBGGRR
        'anim': 'pop',
    },
    'preset_monoline': {
        'label': 'Monoline',
        'desc': 'Hollow outline text (transparent fill, colored outline)',
        'monoline': True,
        'box': False,
    },
    'preset_textbox': {
        'label': 'Text Box',
        'desc': 'Solid box behind the caption line',
        'box': True,
        'box_opacity': 0.85,
        'box_pad': 14,
    },
    'preset_trending': {
        'label': 'Trending Yellow-White',
        'desc': 'Spoken word + your keyword list in yellow, rest white',
        'secondary': '&H002FF3FE',  # #FEF32F yellow in ASS BBGGRR
        'keywords': [],  # filled from the picker UI
    },
}

CAPTION_MOTIONS = {
    'motion_default': {
        'label': 'Style default',
        'desc': 'The structural style\u2019s own animation (no override)',
    },
    'motion_bounce': {
        'label': 'Single Line Bounce',
        'desc': 'Whole line bounces in, word pops 1.3x',
        'entrance': 'bouncein',
        'anim': 'bounce_single',
    },
    'motion_negrow': {
        'label': 'Negative Grow',
        'desc': 'Spoken word slams in big and stays big',
        'anim': 'negrow',
    },
    'motion_flip3d': {
        'label': 'Flip 3D',
        'desc': 'Spoken word flips in on the Y axis',
        'anim': 'flip',
    },
    'motion_typewriter': {
        'label': 'Typewriter',
        'desc': 'Words appear with a block cursor, no scaling',
        'anim': 'none',
        'entrance': 'none',
        'cursor': '\u258c',
    },
    'motion_karaoke': {
        'label': 'Karaoke sweep',
        'desc': 'Smooth color sweep across each word as spoken',
        'highlight_mode': 'karaoke',
        'karaoke_smooth': True,
    },
}


def compose_template(style_id, preset_id='preset_classic',
                     motion_id='motion_default', keywords=None):
    """Merge structural style + visual preset + motion into one template.

    Returns a NEW dict (the style library is never mutated). Priority:
    style < preset < motion. keywords: user word list for the Trending
    preset (also usable standalone). Structural highlight engines that
    own their layout ('wordbox_each') keep it: preset/motion
    highlight_mode overrides are skipped for them.
    """
    base = CAPTION_TEMPLATES[style_id]
    t = dict(base)
    preset = CAPTION_PRESETS.get(preset_id, {})
    motion = CAPTION_MOTIONS.get(motion_id, {})
    structural = base.get('highlight_mode') == 'wordbox_each'
    for ov in (preset, motion):
        for k, v in ov.items():
            if k in ('label', 'desc'):
                continue
            if structural and k == 'highlight_mode':
                continue
            t[k] = v
    if keywords:
        t['keywords'] = list(keywords)
    t['label'] = '%s + %s + %s' % (
        base.get('label', style_id),
        CAPTION_PRESETS.get(preset_id, {}).get('label', preset_id),
        CAPTION_MOTIONS.get(motion_id, {}).get('label', motion_id))
    t['composed_from'] = (style_id, preset_id, motion_id)
    return t

CAPTION_TEMPLATE_CATEGORIES = ['Trending', 'Classic', 'NEW', 'Hits', 'Word', 'Glow', 'Basic', 'Aesthetic', 'Monoline', 'Multiline', 'Highlight', 'B&W', 'Boxed']

DEFAULT_CAPTION_TEMPLATE = "spec_highlight"


# ---------------------------------------------------------------------------
# HEADLINE PRESETS — "main text" overlay styles.
#
# REDESIGNED 2026-10-01 from the 250-video main-text research
# (~/workspace/research/main_text_250.md): 168 rows with verified on-screen
# text, every video 500k+ views and <1 year old, individual creators.
# Ranked findings driving these presets:
#   - ALL CAPS: 127/168 (76%) -> every preset uppercase=True
#   - Heavy/black weight 61%; top fonts Anton (45), Archivo Black (26),
#     Oswald (9) -> Anton/Archivo/Oswald presets lead
#   - White #FFFFFF fill 73% -> white primary everywhere
#   - Red = universal accent (25), then yellow (13)
#   - NO box beats box ~2:1 (97 vs 47) -> default is boxless; box only for
#     breaking/broadcast bars and Johnny-Harris-style pills
#   - Thick black stroke/outline replaces boxes for 30% -> outline 6-8
#   - Size HUGE (~15%+ of frame height): 48% -> size_frac 0.07-0.085 default
#   - Position: left (73), center (68), bottom (60) -> center/left default
#   - Animation: snappy pop/scale-in + hard cuts, NEVER slow fades
#     (verified: Ali Abdaal pop/scale; news stings hard slide/wipe)
#   - On-screen headline = SHORTER, PUNCHIER hook than the video title
#     (make_overlay_text already caps at 5 words)
#   - House-style lock-in: creators run ONE fixed template -> strong default
#
# Colors are ASS &HAABBGGRR. size_frac is a fraction of the play height.
# entrance: pop | wipe_left | rise | (fade removed per research)
# All fonts bundled in assets/fonts/.
# ---------------------------------------------------------------------------
HEADLINE_PRESETS = {
    "creator": {
        "label": "Creator Impact",
        "desc": "Research #1 style: huge Anton caps, white, red keyword, thick black outline, no box, pops in",
        "font": "Anton", "size_frac": 0.080,
        "primary": "&H00FFFFFF", "accent": "&H002828FF",  # red #FF2828
        "box": False, "pill": False, "back_c": "&H00000000",
        "outline": 7, "shadow": 2,
        "alignment": 8, "margin_v_frac": 0.10,
        "entrance": "pop", "uppercase": True,
    },
    "archivo": {
        "label": "Archivo Punch",
        "desc": "Archivo Black caps, white, yellow keyword, thick outline, no box, pops in",
        "font": "Archivo Black", "size_frac": 0.075,
        "primary": "&H00FFFFFF", "accent": "&H0000EAFF",  # yellow #FFEA00
        "box": False, "pill": False, "back_c": "&H00000000",
        "outline": 7, "shadow": 2,
        "alignment": 8, "margin_v_frac": 0.10,
        "entrance": "pop", "uppercase": True,
    },
    "oswald": {
        "label": "Oswald Cond.",
        "desc": "Condensed Oswald caps, white, red keyword, outline, no box, pops in",
        "font": "Oswald", "size_frac": 0.075,
        "primary": "&H00FFFFFF", "accent": "&H002828FF",  # red #FF2828
        "box": False, "pill": False, "back_c": "&H00000000",
        "outline": 6, "shadow": 2,
        "alignment": 8, "margin_v_frac": 0.10,
        "entrance": "pop", "uppercase": True,
    },
    "breaking": {
        "label": "Breaking Bar",
        "desc": "Red breaking-news bar, condensed bold white, wipes in from the left",
        "font": "Anton", "size_frac": 0.055,
        "primary": "&H00FFFFFF", "accent": "&H00FFFFFF",
        "box": True, "pill": False, "back_c": "&H002E10C8",  # #C8102E red
        "outline": 2, "shadow": 0,
        "alignment": 8, "margin_v_frac": 0.055,
        "entrance": "wipe_left", "uppercase": True,
    },
    "viral": {
        "label": "Viral Title",
        "desc": "Huge condensed title, thick outline, pops in with scale",
        "font": "Bebas Neue", "size_frac": 0.075,
        "primary": "&H00FFFFFF", "accent": "&H0000EAFF",  # yellow #FFEA00
        "box": False, "pill": False, "back_c": "&H00000000",
        "outline": 7, "shadow": 2,
        "alignment": 8, "margin_v_frac": 0.07,
        "entrance": "pop", "uppercase": True,
    },
    "pill": {
        "label": "Creator Pill",
        "desc": "Rounded black pill, bold white caps, yellow keyword accents, pops in",
        "font": "Poppins", "size_frac": 0.045,
        "primary": "&H00FFFFFF", "accent": "&H0000EAFF",  # yellow #FFEA00
        "box": False, "pill": True, "back_c": "&H40000000",  # black @75%
        "outline": 0, "shadow": 1,
        "alignment": 8, "margin_v_frac": 0.055,
        "entrance": "pop", "uppercase": True,
    },
    "broadcast": {
        "label": "Broadcast Lower",
        "desc": "Navy translucent bar, extra-bold white, gold keyword accents",
        "font": "Montserrat", "size_frac": 0.042,
        "primary": "&H00FFFFFF", "accent": "&H002CC7FF",  # gold #FFC72C
        "box": True, "pill": False, "back_c": "&H26331D0B",  # #0B1D33 @85%
        "outline": 1, "shadow": 1,
        "alignment": 8, "margin_v_frac": 0.055,
        "entrance": "wipe_left", "uppercase": True,
    },
    "minimal": {
        "label": "Clean Minimal",
        "desc": "No box, bold white caps with soft shadow, quick pop-in",
        "font": "Montserrat", "size_frac": 0.045,
        "primary": "&H00FFFFFF", "accent": "&H00FFF97D",  # cyan #7DF9FF
        "box": False, "pill": False, "back_c": "&H00000000",
        "outline": 0, "shadow": 2,
        "alignment": 8, "margin_v_frac": 0.055,
        "entrance": "pop", "uppercase": True,
    },
}

DEFAULT_HEADLINE_PRESET = "creator"

# ---------------------------------------------------------------------------
# NICHE-AWARE HEADLINES — from the user's 17-video deep-watch
# (~/workspace/research/user_videos_17.md) + the 250-video study.
#
# What the 17 videos teach, per niche:
#   - KAUN SA TEXT: har beat ka sab se quotable NUMBER / DATE / NAME / TERM
#     bara text banta hai ("voice explains, text anchors"). Listicles mein
#     ~85-90% points textified, explainers ~40-65%.
#   - KAUN SA COLOR: white fill default; YELLOW/GOLD = universal accent
#     (numbers, bars, ID labels); RED = urgency/keyword accent.
#   - KAHAN: center = bare headlines; bottom-third = labels/chyrons;
#     bottom-right = countdown headers.
#   - NICHE TWISTS: military (Military Blue) = yellow ID labels + red
#     urgency + huge condensed stat headlines; finance (Cole Mercer) =
#     gold money accents + big numbers; health listicles (Survival Roots)
#     = yellow countdown bars; tech = bold condensed.
# ---------------------------------------------------------------------------

NICHE_KEYWORDS = {
    "finance": ("money", "dollar", "stock", "market", "fed", "bank", "invest",
                "crypto", "bitcoin", "billion", "million", "economy", "debt",
                "inflation", "savings", "trading", "wall street"),
    "military": ("war", "drone", "missile", "army", "military", "strike",
                 "convoy", "weapon", "troops", "ukraine", "russia", "attack",
                 "battle", "tank", "navy", "airforce"),
    "health": ("health", "doctor", "disease", "supplement", "muscle", "diet",
               "cancer", "vitamin", "protein", "iron", "shampoo", "spine",
               "exercise", "medical"),
    "tech": ("ai", "tech", "phone", "speaker", "software", "robot", "app",
             "breakthrough", "invention", "digital", "chip", "jbl"),
    "sports": ("game", "match", "player", "goal", "team", "season", "coach",
               "league", "championship", "score"),
    "crime": ("murder", "police", "crime", "suspect", "arrest", "killer",
              "victim", "court", "prison", "investigation"),
    "history": ("ancient", "empire", "century", "war", "king", "civilization",
                "medieval", "battle", "dynasty", "historical"),
}

# niche -> (headline preset, accent ASS color, why)
NICHE_HEADLINES = {
    "finance":  ("archivo",  "&H002CC7FF", "gold money accents + big numbers"),
    "military": ("creator",  "&H0000EAFF", "yellow ID labels + red urgency"),
    "health":   ("creator",  "&H0000EAFF", "yellow countdown bars"),
    "tech":     ("oswald",   "&H0000EAFF", "bold condensed + yellow"),
    "sports":   ("creator",  "&H0000EAFF", "yellow highlight"),
    "crime":    ("creator",  "&H002828FF", "red urgency"),
    "history":  ("oswald",   "&H002CC7FF", "gold era accents"),
    "general":  ("creator",  "&H002828FF", "white + red keyword (research #1)"),
}


def detect_niche(text):
    """Keyword-based niche detection for headline styling.

    Word-boundary matching throughout: without it "king" fires inside
    "breaking" and "ai" fires inside "said" (the same bug class fixed in
    audio_design._kw_hit).
    """
    low = (text or "").lower()
    best, best_hits = "general", 0
    for niche, kws in NICHE_KEYWORDS.items():
        hits = sum(1 for k in kws
                   if re.search(r"\b" + re.escape(k) + r"\b", low))
        if hits > best_hits:
            best, best_hits = niche, hits
    return best


# Scene text that forces the breaking style (auto mode).
BREAKING_KEYWORDS = ("breaking", "urgent", "alert", "emergency",
                     "just in", "developing", "explosion", "attack")


def headline_preset_for(scene_text, override="auto"):
    """Pick a headline preset id: explicit override, else breaking when the
    scene text carries urgency, else the niche's researched style."""
    if override and override != "auto" and override in HEADLINE_PRESETS:
        return override
    low = (scene_text or "").lower()
    if any(k in low for k in BREAKING_KEYWORDS):
        return "breaking"
    niche = detect_niche(scene_text)
    preset = NICHE_HEADLINES.get(niche, NICHE_HEADLINES["general"])[0]
    return preset if preset in HEADLINE_PRESETS else DEFAULT_HEADLINE_PRESET


def headline_accent_for(scene_text, override="auto"):
    """Niche accent color (ASS &HAABBGGRR) for the headline keywords."""
    if override and override != "auto" and override in HEADLINE_PRESETS:
        return HEADLINE_PRESETS[override]["accent"]
    low = (scene_text or "").lower()
    if any(k in low for k in BREAKING_KEYWORDS):
        return HEADLINE_PRESETS["breaking"]["accent"]
    niche = detect_niche(scene_text)
    return NICHE_HEADLINES.get(niche, NICHE_HEADLINES["general"])[1]


def caption_template_labels():
    """Ordered (key, label) pairs for UI dropdowns."""
    return [(k, v["label"]) for k, v in CAPTION_TEMPLATES.items()]


def headline_preset_labels():
    """Ordered (key, label) pairs for the main-text style dropdown."""
    return [("auto", "Auto (breaking when urgent)")] + [
        (k, v["label"]) for k, v in HEADLINE_PRESETS.items()]


def make_overlay_text(analysis, max_words=5):
    """Short punchy overlay, never the whole sentence.

    Text-worthiness rule (from the 17-video deep-watch): the single most
    quotable NUMBER / DATE / NAME / TERM per beat gets the big text —
    "voice explains, text anchors". So number-bearing phrases win over
    generic keywords.
    """
    text = getattr(analysis, "text", "") or ""
    # 1) number spans: $40, 68B, 48,000 tons, 90%, 2026, 3.5 million ...
    num_spans = []
    for m in re.finditer(
            r"\$?\d[\d,]*\.?\d*(?:\s*(?:billion|million|thousand|b\b|m\b|k\b|%|percent|tons?|tonnes?))?"
            r"|\b(?:19|20)\d{2}\b", text, re.IGNORECASE):
        num_spans.append(m.group(0).strip())
    parts = []
    for ns in num_spans:
        for w in ns.split():
            if w.lower() not in [x.lower() for x in parts]:
                parts.append(w)
    # 2) phrases (often carry the NAME/TERM)
    for p in analysis.phrases or []:
        for w in p.split():
            if w.lower() not in [x.lower() for x in parts]:
                parts.append(w)
    # 3) keywords fill the rest
    for k in analysis.keywords or []:
        if k.lower() not in [x.lower() for x in parts]:
            parts.append(k)
    return " ".join(parts[:max_words]).upper()


# ---------------------------------------------------------------------------
# SPEC DATA MODEL — CapCut auto-caption engine (Step 1 of user spec).
#
# Words come from Whisper/faster-whisper with word_timestamps=True:
#   { word: str, start: float (sec, 3 decimals), end: float,
#     confidence: float }
# chunk_words() groups them per the spec's chunking rule. word_state_at()
# implements the 3-state machine (active/past/upcoming) that drives fill
# color, scale and glow per style config.
# ---------------------------------------------------------------------------
_SENTENCE_END = frozenset(".!?")

def chunk_words(words, max_words=4, max_dur=2.5, gap_break=0.4):
    """Group word-timestamp dicts into caption chunks.

    Break a chunk at: sentence punctuation (. ! ?), gap between words >
    gap_break seconds, or chunk duration > max_dur seconds. Caps chunk at
    max_words words (3-4 for 9:16 shorts, 7 for 16:9 landscape).
    Returns [{ words: [...], start: float, end: float }, ...].
    """
    chunks = []
    cur = []
    for w in words:
        if cur:
            prev = cur[-1]
            gap = w["start"] - prev["end"]
            dur = w["end"] - cur[0]["start"]
            if (len(cur) >= max_words or gap > gap_break
                    or dur > max_dur
                    or prev["word"][-1:] in _SENTENCE_END):
                chunks.append({"words": list(cur),
                               "start": cur[0]["start"],
                               "end": cur[-1]["end"]})
                cur = []
        cur.append(w)
    if cur:
        chunks.append({"words": list(cur),
                       "start": cur[0]["start"],
                       "end": cur[-1]["end"]})
    return chunks


def word_state_at(word, t):
    """3-state machine for one word at frame time t.

    Returns "active" (t within [start, end]), "past" (t > end) or
    "upcoming" (t < start).
    """
    if t < word["start"]:
        return "upcoming"
    if t > word["end"]:
        return "past"
    return "active"


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
    if anim == "popin":
        # spec popIn: fast 108% punch in 80ms (back-ease approximated with
        # accel<1 fast-start), holds — for word-by-word sync
        return (c_open + "\\t(0,80,0.7,\\fscx108\\fscy108)}", c_close)
    if anim == "pop12":
        # spec highlight active: scaleTo 1.12 in 120ms, spring-ish settle,
        # reverts to 100% exactly at word end
        return (c_open + "\\t(0,120,0.8,\\fscx112\\fscy112)"
                "\\t(120,300,\\fscx100\\fscy100)}", c_close)
    if anim == "pop106":
        # spec dynamic active: scaleTo 1.06 in 150ms, reverts at word end
        return (c_open + "\\t(0,150,0.75,\\fscx106\\fscy106)"
                "\\t(150,300,\\fscx100\\fscy100)}", c_close)
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
    if anim == "bounce_single":
        anim = "bounce"  # motion alias: single-line bounce == glow bounce
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


def _entrance_tags(entrance, alignment, pw, ph, mv=60, dx=0, dy=0):
    """Line-entrance animation tags (prepended to the event text).

    Uses ONLY \\fad and \\move so it never conflicts with the word-level
    \\t(\\fscx/\\fscy/\\1c) transforms. mv = style MarginV (px).
    dx/dy = user drag offset in px (click-drag captions on the preview);
    baked into the \\move endpoints so entrances stay intact.
    """
    if entrance == "fadein":
        return "{\\fad(180,0)}"
    if entrance == "fade02":
        # spec minimal: plain 0.2s linear fade, no motion
        return "{\\fad(200,0)}"
    if entrance == "fade01":
        # spec standard subtitles: max 0.1s fade (or instant)
        return "{\\fad(100,0)}"
    if entrance == "rise300":
        # spec dynamic slideUp: 300ms spring-ish rise + fade
        if alignment == 8:
            x, y = pw // 2, mv
        elif alignment == 5:
            x, y = pw // 2, ph // 2
        else:
            x, y = pw // 2, ph - mv
        x, y = x + dx, y + dy
        return "{\\move(%d,%d,%d,%d,0,300)\\fad(300,0)}" % (x, y + 60, x, y)
    if entrance == "slideleft":
        # spec dynamic slideLeft: slide in from the right + fade, 300ms
        if alignment == 8:
            x, y = pw // 2, mv
        elif alignment == 5:
            x, y = pw // 2, ph // 2
        else:
            x, y = pw // 2, ph - mv
        x, y = x + dx, y + dy
        return "{\\move(%d,%d,%d,%d,0,300)\\fad(300,0)}" % (x + 90, y, x, y)
    if entrance == "bouncein":
        # spec dynamic bounceIn: overshoot 118% then settle, 300ms.
        # Uses \\fscx like word anims; only on the chunk's first word-event,
        # so the whole line bounces in once per chunk.
        return "{\\fad(300,0)\\t(0,180,\\fscx118\\fscy118)" \
               "\\t(180,300,\\fscx100\\fscy100)}"
    if entrance == "fadeup":
        # spec aesthetic fadeUp: gentle rise 40px + fade, 350ms, easeOut
        if alignment == 8:
            x, y = pw // 2, mv
        elif alignment == 5:
            x, y = pw // 2, ph // 2
        else:
            x, y = pw // 2, ph - mv
        x, y = x + dx, y + dy
        return "{\\move(%d,%d,%d,%d,0,350)\\fad(350,0)}" % (x, y + 40, x, y)
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
        x, y = x + dx, y + dy
        return "{\\move(%d,%d,%d,%d,0,250)\\fad(250,0)}" % (x, y + 50, x, y)
    return ""


def _font_file_for(font_name):
    """Best bundled TTF for a font family name (used for PIL measuring)."""
    try:
        from .app_paths import fonts_dir
    except Exception:
        return None
    d = fonts_dir()
    if not d:
        return None
    norm = re.sub(r"[^a-z]", "", (font_name or "").lower())
    fallback = None
    for p in sorted(d.glob("*.ttf")):
        pn = re.sub(r"[^a-z]", "", p.stem.lower())
        if pn.startswith(norm) or norm in pn:
            if any(k in pn for k in ("extrabold", "black", "bold")):
                return p
            fallback = fallback or p
    return fallback


def _headline_entrance(kind, pw, ph, margin_v):
    """ASS entrance tags for a headline preset (alignment 8 = top-center,
    so the anchor Y is the style's MarginV)."""
    xc, y = pw // 2, margin_v
    if kind == "wipe_left":
        return "{\\an8\\move(%d,%d,%d,%d,0,350)}" % (-pw // 2, y, xc, y)
    if kind == "rise":
        return "{\\an8\\move(%d,%d,%d,%d,0,350)}" % (xc, y + 90, xc, y)
    if kind == "pop":
        return "{\\an8\\fscx125\\fscy125\\t(0,230,\\fscx100\\fscy100)}"
    if kind == "rise_fade":
        return ("{\\an8\\fad(200,150)\\move(%d,%d,%d,%d,0,350)}"
                % (xc, y + 90, xc, y))
    return "{\\an8\\fad(250,150)}"


def _headline_style_line(pid, hp, ph):
    """One V4+ Style line for a headline preset."""
    size = max(28, int(ph * hp["size_frac"]))
    mv = int(ph * hp["margin_v_frac"])
    border = 4 if hp.get("pill") else (3 if hp.get("box") else 1)
    return ("Style: HL_%s,%s,%d,%s,%s,&H00000000,%s,-1,0,0,0,100,100,0,0,"
            "%d,%d,%d,8,60,60,%d,1"
            % (pid, hp["font"], size, hp["primary"], hp["primary"],
               hp["back_c"], border, hp["outline"], hp["shadow"], mv))


def build_srt(sentence_timings, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sentence_timings, 1):
            f.write(f"{i}\n{_ts_srt(s['start'])} --> {_ts_srt(s['end'])}\n{s['text']}\n\n")
    return path


def build_ass(sentence_timings, word_timings, path, font_size=48, highlight=True,
              overlays=None, play_res=(1920, 1080), template=None,
              secondary_track=None):
    """Build the ASS file: bottom captions + optional top overlays.

    overlays: list of dicts {text, start, end, style?, keywords?}.
    style: a HEADLINE_PRESETS id (creator / archivo / oswald / breaking /
    viral / pill / broadcast / minimal) — redesigned 2026-10-01 from the
    250-video main-text research: huge heavy caps, white fill, red/yellow
    keyword accents, no box by default, snappy pop entrances. Falls back
    to the default creator style.
    template: key from CAPTION_TEMPLATES (CapCut-style caption look).
    secondary_track: for dual-language templates, list of dicts
    {text, start, end} with GENUINE second-language text (never a copy of
    the primary). Rendered with the "Cap2" style at its own y position.
    """
    # template may be a key into CAPTION_TEMPLATES or an already-composed
    # dict from compose_template(style, preset, motion)
    if isinstance(template, dict):
        tmpl = template
    else:
        tmpl = CAPTION_TEMPLATES.get(template or DEFAULT_CAPTION_TEMPLATE,
                                     CAPTION_TEMPLATES[DEFAULT_CAPTION_TEMPLATE])
    pw, ph = play_res
    # user_scale: click-drag resize factor from the preview (1.0 = unchanged)
    cap_size = max(24, int(ph * font_size / 1080 * tmpl["size_scale"]
                           * tmpl.get("user_scale", 1.0)))
    top_size = max(30, int(ph / 20))
    # monoline preset: hollow outline — transparent fill, outline takes the
    # text color. Alpha persists through {\c} overrides in events.
    h_primary, h_outline_c, h_outline = (tmpl["primary"], tmpl["outline_c"],
                                         tmpl["outline"])
    if tmpl.get("monoline"):
        h_primary = "&HFF" + tmpl["primary"][4:]
        h_outline_c = tmpl["primary"]
        h_outline = max(tmpl["outline"], 3)
    # BorderStyle: 3 = opaque box (Hormozi), 4 = rounded box (pill),
    # else 1 = outline.
    if tmpl.get("pill"):
        border_style = 4
    elif tmpl["box"]:
        border_style = 3
    else:
        border_style = 1
    tmpl["_cap_size"] = cap_size  # for wordbig static fallback sizing
    # spec styles can pin the caption line to an exact y fraction
    # (e.g. y=0.78 -> MarginV = 0.22 * frame height); default keeps 60px.
    mv = int(ph * tmpl["margin_v_frac"]) if "margin_v_frac" in tmpl else 60
    # Interactive caption move/resize (click-drag on the preview):
    # dx_frac/dy_frac are fractions of the play resolution; positive dy
    # moves captions DOWN on screen. Applied via \pos so every style
    # (including \move entrances, which get the offset baked in) shifts
    # exactly like the preview.
    dx_px = int(round(tmpl.get("dx_frac", 0.0) * pw))
    dy_px = int(round(tmpl.get("dy_frac", 0.0) * ph))
    _al = tmpl.get("alignment", 2)
    if _al == 8:
        _ax, _ay = pw // 2, mv
    elif _al == 5:
        _ax, _ay = pw // 2, ph // 2
    else:
        _ax, _ay = pw // 2, ph - mv

    def _pos_shift(ent_text):
        """{pos} prefix reproducing the default anchor + user drag offset.

        Skipped when the event already positions itself (\\move entrance
        has the offset baked in; \\pos word-boxes get it directly), and
        skipped entirely when nothing was dragged (zero behavior change).
        """
        if dx_px == 0 and dy_px == 0:
            return ""
        if "\\move" in ent_text or "\\pos" in ent_text:
            return ""
        return "{\\pos(%d,%d)}" % (_ax + dx_px, _ay + dy_px)
    # CapBox: opaque per-word box style (spec style 3). BorderStyle=3 draws
    # the box from BackColour; Outline pads it (spec paddingY 12).
    box_op = tmpl.get("box_opacity", 0.85)
    box_back = "&H%02X000000" % int(round(255 * (1 - box_op)))
    box_pad = tmpl.get("box_pad", 14)
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,%s,%d,%s,%s,%s,%s,%d,%d,0,0,100,100,%d,0,%d,%d,%d,%d,40,40,%d,1
Style: CapBox,%s,%d,%s,%s,%s,%s,%d,%d,0,0,100,100,%d,0,3,%d,0,5,40,40,%d,1
Style: Top,Arial,%d,&H00FFFFFF,&H000019FF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,60,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (pw, ph, tmpl["font"], cap_size, h_primary, tmpl["secondary"],
       h_outline_c, tmpl["back_c"], tmpl["bold"], tmpl["italic"],
       tmpl.get("spacing", 0),
       border_style, h_outline, tmpl["shadow"], tmpl["alignment"], mv,
       tmpl["font"], cap_size, tmpl["primary"], tmpl["secondary"],
       tmpl["outline_c"], box_back, tmpl["bold"], tmpl["italic"],
       tmpl.get("spacing", 0),
       box_pad, mv,
       top_size)
    # Headline preset styles (one V4+ Style per preset actually used by the
    # overlays). The legacy plain "Top" style stays for compatibility.
    _used_hl = []
    for _ov in overlays or []:
        _pid = _ov.get("style") or DEFAULT_HEADLINE_PRESET
        if _pid not in HEADLINE_PRESETS:
            _pid = DEFAULT_HEADLINE_PRESET
        if _pid not in _used_hl:
            _used_hl.append(_pid)
    _hl_lines = "".join(
        _headline_style_line(_pid, HEADLINE_PRESETS[_pid], ph) + "\n"
        for _pid in _used_hl)
    header = header.replace("[Events]", _hl_lines + "[Events]")
    # spec style 8 dual-language: second independent track ("Cap2" style)
    # with its own font/size/y. Genuine translation input required —
    # never duplicate the primary line.
    if tmpl.get("dual"):
        sc = tmpl.get("secondary_cfg", {})
        sec_size = int(ph * font_size / 1080 * sc.get("size_scale", 0.92))
        sec_mv = int(ph * sc.get("margin_v_frac", 0.12))
        cap2 = ("Style: Cap2,%s,%d,%s,%s,&H00000000,&H00000000,0,0,0,0,"
                "100,100,0,0,1,%d,0,2,40,40,%d,1\n" % (
                    sc.get("font", "Montserrat"), sec_size,
                    sc.get("fill", "&H00D0D0D0"), sc.get("fill", "&H00D0D0D0"),
                    sc.get("outline", 3), sec_mv))
        header = header.replace("[Events]",
                                cap2 + "[Events]")
    lines = []
    hmode = tmpl.get("highlight_mode", "karaoke")
    anim = tmpl.get("anim", "pop")
    entrance = tmpl.get("entrance", "slideup")
    # spec dynamic: entry animation cycles per chunk index
    # (e.g. ['rise300','bouncein','slideleft'])
    entrance_cycle = tmpl.get("entrance_cycle")
    tcase = tmpl.get("text_case")

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
        # spec 3-state machine: past/upcoming words get their own fills
        # (default = primary when the style does not define them)
        past_c = tmpl.get("past", pri)
        up_c = tmpl.get("upcoming", pri)
        # trending preset: user keyword list — these words render in the
        # active (secondary) color in every state, not just when spoken
        kw_set = set(k.strip().lower()
                     for k in (tmpl.get("keywords") or []) if k.strip())
        # typewriter motion: block cursor after the spoken word
        cursor = tmpl.get("cursor", "")
        words = list(word_timings)
        wi = 0
        for ci, s in enumerate(sentence_timings):
            if entrance_cycle:
                ent_name = entrance_cycle[ci % len(entrance_cycle)]
            else:
                ent_name = entrance
            seg = []
            while wi < len(words) and words[wi]["end"] <= s["end"] + 0.05:
                seg.append(words[wi])
                wi += 1
            if not seg:
                w0 = _case(s["text"])
                safe = sanitize_ass_text(w0)
                ent = _entrance_tags(ent_name, tmpl["alignment"], pw, ph,
                                     dx=dx_px, dy=dy_px)
                lines.append(
                    f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                    f"Cap,,0,0,0,,{_pos_shift(ent)}{ent}{safe}")
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
                ent = _entrance_tags(ent_name, tmpl["alignment"], pw, ph, mv,
                                     dx=dx_px, dy=dy_px) if i == 0 else ""
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
                        if cursor:
                            parts[-1] += "{\\c%s}%s" % (sec, cursor)
                    elif j < i:
                        # past: spoken already (keywords stay highlighted)
                        kc = sec if sw_.lower() in kw_set else past_c
                        parts.append("{\\c%s}%s{\\c%s}" % (kc, safe, pri))
                    else:
                        # upcoming: not yet spoken
                        kc = sec if sw_.lower() in kw_set else up_c
                        parts.append("{\\c%s}%s{\\c%s}" % (kc, safe, pri))
                txt = " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},"
                    f"Cap,,0,0,0,,{_pos_shift(ent)}{ent}{txt}")
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
                    f"Cap,,0,0,0,,{_pos_shift('')}{safe}")
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
            ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph,
                                 dx=dx_px, dy=dy_px)
            safe = sanitize_ass_text(_case(w["word"]))
            lines.append(
                f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},"
                f"Cap,,0,0,0,,{_pos_shift(ent)}{ent}{wopen}{safe}{wclose}")
    elif highlight and word_timings and hmode == "dual":
        # Dual-Language Captions: original line + second line below.
        # Second line reuses the sentence text (user's translation/Urdu can
        # replace it); rendered smaller via \fs.
        dual_size = max(18, int(cap_size * 0.62))
        for s in sentence_timings:
            safe = sanitize_ass_text(_case(s["text"]))
            ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph,
                                 dx=dx_px, dy=dy_px)
            # main line
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{_pos_shift(ent)}{ent}{safe}")
            # second language line (smaller, secondary color)
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{_pos_shift('')}{{\\fs{dual_size}\\c{tmpl['secondary']}}}"
                f"{safe}{{\\c{tmpl['primary']}}}")
    elif highlight and word_timings and hmode == "standard":
        # Standard Subtitles: plain two-line text, no highlight, no anim.
        for s in sentence_timings:
            safe = sanitize_ass_text(_case(s["text"]))
            lines.append(
                f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},"
                f"Cap,,0,0,0,,{_pos_shift('')}{safe}")
    elif highlight and word_timings and hmode == "wordbox_each":
        # Spec style 3 UPPERCASE IMPACT: EVERY word gets its OWN black box.
        # Box+word slam in at word.start (scale 1.25 -> 1.0 in 100ms,
        # easeOut); words stay visible once spoken. PIL measures each word
        # so the \pos boxes sit inline, centered as one line.
        y = int(ph * (1 - tmpl.get("margin_v_frac", 0.25)))
        try:
            from PIL import ImageFont as _IF
            _fp = _font_file_for(tmpl["font"])
            _measure = _IF.truetype(str(_fp), cap_size) if _fp else None
        except Exception:
            _measure = None
        for s in sentence_timings:
            seg = [w for w in word_timings
                   if w["end"] > s["start"] and w["start"] < s["end"]]
            if not seg:
                continue
            disp = [_case(w["word"]) for w in seg]
            if _measure:
                widths = [_measure.getlength(t) for t in disp]
                space = _measure.getlength(" ")
            else:
                widths = [cap_size * 0.6 * len(t) for t in disp]
                space = cap_size * 0.6
            total = sum(widths) + space * (len(disp) - 1)
            x = pw / 2 - total / 2
            for k, w in enumerate(seg):
                cx = int(x + widths[k] / 2) + dx_px
                x += widths[k] + space
                ws, we = w["start"], w["end"]
                if k + 1 < len(seg):
                    we = seg[k + 1]["start"]
                else:
                    we = max(we, s["end"])
                if we <= ws:
                    we = ws + 0.01
                safe = sanitize_ass_text(disp[k])
                tag = ("{\\an5\\pos(%d,%d)\\fscx125\\fscy125"
                       "\\t(0,100,0.6,\\fscx100\\fscy100)}" % (cx, y + dy_px))
                lines.append(
                    f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},"
                    f"CapBox,,0,0,0,,{tag}{safe}")
    elif highlight and word_timings:
        # karaoke-style per-word highlight
        k_tag = "\\kf" if tmpl.get("karaoke_smooth") else "\\k"
        ent = _entrance_tags(entrance, tmpl["alignment"], pw, ph,
                             dx=dx_px, dy=dy_px)
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
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{_pos_shift(ent)}{ent}{txt.strip()}")
    else:
        for s in sentence_timings:
            safe = sanitize_ass_text(s["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{_pos_shift('')}{safe}")
    # spec style 8: secondary track events (independent text + timings,
    # Cap2 style). Static per spec (no word animation on secondary).
    if tmpl.get("dual") and secondary_track:
        # secondary anchor: an2 with its own MarginV
        _sec_mv = int(ph * tmpl.get("secondary_cfg", {}).get("margin_v_frac", 0.12))
        _sec_pos = ("" if (dx_px == 0 and dy_px == 0) else
                    "{\\pos(%d,%d)}" % (pw // 2 + dx_px, ph - _sec_mv + dy_px))
        for t2 in secondary_track:
            safe2 = sanitize_ass_text(t2["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(t2['start'])},{_ts_ass(t2['end'])},"
                         f"Cap2,,0,0,0,,{_sec_pos}{safe2}")
    for ov in overlays or []:
        txt = ov.get("text", "") or ""
        if not txt.strip():
            continue
        pid = ov.get("style") or DEFAULT_HEADLINE_PRESET
        hp = HEADLINE_PRESETS.get(pid, HEADLINE_PRESETS[DEFAULT_HEADLINE_PRESET])
        if pid not in HEADLINE_PRESETS:
            pid = DEFAULT_HEADLINE_PRESET
        if hp.get("uppercase"):
            txt = txt.upper()
        kwset = {str(k).lower() for k in (ov.get("keywords") or [])}
        accent = ov.get("accent") or hp["accent"]
        parts = []
        for w_ in txt.split():
            core = w_.strip(".,!?\"'").lower()
            safe = sanitize_ass_text(w_)
            if core and core in kwset and accent != hp["primary"]:
                parts.append("{\\c%s}%s{\\c%s}"
                             % (accent, safe, hp["primary"]))
            else:
                parts.append(safe)
        ent = _headline_entrance(hp.get("entrance", "fade"), pw, ph,
                                 int(ph * hp["margin_v_frac"]))
        lines.append(f"Dialogue: 1,{_ts_ass(ov['start'])},{_ts_ass(ov['end'])},"
                     f"HL_{pid},,0,0,0,,{ent}{' '.join(parts)}")
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
