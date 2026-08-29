import type { Language } from "./types";

/** Small hand-kept dictionary. Enough for the nav, headings and CTAs to flip
 *  between English and Marathi on camera. */
const DICT = {
  brand: { en: "Bhav Setu", mr: "भाव सेतू" },
  tagline: { en: "SELLING DECISIONS FOR FARMERS", mr: "शेतकऱ्यांसाठी विक्री निर्णय" },
  nav_home: { en: "Home", mr: "मुख्यपृष्ठ" },
  nav_dashboard: { en: "Dashboard", mr: "डॅशबोर्ड" },
  nav_community: { en: "Community", mr: "समुदाय" },
  nav_history: { en: "History", mr: "इतिहास" },
  nav_advisor: { en: "Advisor", mr: "सल्लागार" },
  nav_compare: { en: "Compare", mr: "तुलना" },
  nav_irrigation: { en: "Irrigation", mr: "पाणी" },
  nav_accuracy: { en: "Accuracy", mr: "अचूकता" },
  nav_transparency: { en: "Transparency", mr: "पारदर्शकता" },
  nav_chat: { en: "Chat", mr: "संवाद" },
  login: { en: "Log in", mr: "लॉग इन" },
  signup: { en: "Sign up", mr: "नोंदणी" },
  logout: { en: "Log out", mr: "बाहेर पडा" },
  whatsapp_cta: { en: "Continue on WhatsApp", mr: "व्हॉट्सअ‍ॅपवर सुरू ठेवा" },
  get_advice: { en: "Get my selling advice", mr: "मला विक्री सल्ला द्या" },
  net_in_hand: { en: "Net in hand", mr: "हातात मिळणारे" },
  gross_at_mandi: { en: "Gross at mandi", mr: "बाजारातील भाव" },
  confidence: { en: "Confidence", mr: "विश्वास" },
  today: { en: "Today", mr: "आज" },
} as const;

export type DictKey = keyof typeof DICT;

export function t(key: DictKey, lang: Language): string {
  return DICT[key][lang];
}
