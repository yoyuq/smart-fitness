"""Patch pose_engine.py FORM_RULES with peer-reviewed evidence.

Reads pose_engine.py, replaces the block of scoring functions with an updated
version that includes evidence docstrings + refined thresholds, and rewrites the
file preserving the original BOM.
"""
from pathlib import Path

PATH = Path("ml_pose/pose_engine.py")

NEW_BLOCK = '''# ============================================================
# Form-scoring rules with peer-reviewed evidence citations.
# Interior-angle convention: 180 = fully extended, smaller = more flexion.
# Papers usually report *flexion* angles from 0 = extension, so
# "90 knee flexion" in the literature == interior 90 in our code.
# Evidence file (single source of truth for numbers below):
#   docs/rep_completion_algorithm_evidence.md (or the artifact JSON produced by the audit)
# ============================================================

EVIDENCE_SOURCES = {
    "squat.knee_deep":    {"claim": "Parallel squat = interior knee ~80 (=100 flexion); deeper (<60) only cautious with heavy load, not inherently unsafe.", "authors": "Escamilla RF (2001); Hartmann H et al. (2013)", "url": "https://pubmed.ncbi.nlm.nih.gov/11194098/ ; https://pubmed.ncbi.nlm.nih.gov/23821469/"},
    "squat.knee_shallow": {"claim": "Knee flexion <50 (interior >130) is a partial/quarter-squat; parallel (interior ~80) recommended for strength/hypertrophy.", "authors": "Escamilla RF et al. (2001, MSSE 33:1552)", "url": "https://pubmed.ncbi.nlm.nih.gov/11528346/"},
    "squat.torso_tilt":   {"claim": "Greater trunk forward lean increases lumbar shear; 60 cutoff is a coach heuristic.", "authors": "Russell PJ, Phillips SJ (1989, RQES 60:201)", "url": "https://pubmed.ncbi.nlm.nih.gov/2489844/"},
    "push_up.elbow_shallow": {"claim": "Standard push-up bottom ~90 elbow flexion (interior ~90); interior >150 at bottom means chest never approached the floor.", "authors": "Dhahbi W et al. (2022, Sports Biomech 21:1)", "url": "https://pubmed.ncbi.nlm.nih.gov/30284496/"},
    "push_up.elbow_overflex": {"claim": "Deeper elbow flexion raises peak elbow moment; over-flexion (interior <60) is unusual and mainly a joint-load concern.", "authors": "Polovinets O et al. (2026, Handchir 58:243)", "url": "https://pubmed.ncbi.nlm.nih.gov/42269686/"},
    "plank.hip_break":    {"claim": "Prone plank is defined by a straight body line (hip interior ~180); sag <160 or pike >200 is a technique failure.", "authors": "Ekstrom RA et al. (2007, JOSPT 37:754); Moreno-Navarro P et al. (2024, JBMR 37:743)", "url": "https://pubmed.ncbi.nlm.nih.gov/18560185/ ; https://pubmed.ncbi.nlm.nih.gov/38217576/"},
    "lunge.front_knee":   {"claim": "Forward-lunge front knee should reach ~90 flexion (interior ~90); interior >110 = <70 flexion = clearly incomplete.", "authors": "Escamilla RF et al. (2008 JOSPT; 2022 J Appl Biomech)", "url": "https://pubmed.ncbi.nlm.nih.gov/18978453/ ; https://pubmed.ncbi.nlm.nih.gov/35697336/"},
    "lunge.knee_diff":    {"claim": "Left-right knee-flexion asymmetry >=4 is clinically meaningful; a true split-stance lunge should show a large left-right delta.", "authors": "Hall M et al. (2015, The Knee 22:506)", "url": "https://pubmed.ncbi.nlm.nih.gov/25907262/"},
    "jumping_jack.arm":   {"claim": "Jumping jack = full arm abduction 0-180 at glenohumeral joint; arms should clearly reach overhead (>=150).", "authors": "Lam JH, Bordoni B (2023, StatPearls NBK537148)", "url": "https://www.ncbi.nlm.nih.gov/books/NBK537148/"},
    "bicep_curl.rom":     {"claim": "Standard biceps-curl ROM 0-135 elbow flexion (interior 45-180); interior >160 = <20 flexion = no rep started.", "authors": "Pedrosa GF et al. (2023, Sports 11:39)", "url": "https://pubmed.ncbi.nlm.nih.gov/36828324/"},
    "bicep_curl.shoulder_cheat": {"claim": "Active shoulder flexion during a curl reduces biceps demand; degree-cutoff is heuristic.", "authors": "Oliveira LF et al. (2009, JSSM 8:24)", "url": "https://pubmed.ncbi.nlm.nih.gov/24150552/"},
    "shoulder_press.lockout": {"claim": "Full overhead-press lockout: elbow interior ~170-180, glenohumeral elevation ~150-180.", "authors": "Gundersen AH et al. (2025, Sports Biomech online)", "url": "https://pubmed.ncbi.nlm.nih.gov/41335596/"},
    "shoulder_press.torso_arch": {"claim": "Excess trunk deviation from vertical during loaded pressing raises lumbar shear; 30 back-arch cutoff is a heuristic.", "authors": "Russell PJ, Phillips SJ (1989, RQES 60:201)", "url": "https://pubmed.ncbi.nlm.nih.gov/2489844/"},
}


def _score_squat(ang):
    """Squat form score.

    Peer-reviewed evidence:
    - Escamilla 2001 (MSSE 33:127) parallel squat = interior knee ~80. https://pubmed.ncbi.nlm.nih.gov/11194098/
    - Hartmann 2013 (Sports Med 43:993) deeper squats not inherently unsafe. https://pubmed.ncbi.nlm.nih.gov/23821469/
    - Russell 1989 (RQES 60:201) trunk lean increases lumbar shear (60 cutoff = heuristic). https://pubmed.ncbi.nlm.nih.gov/2489844/
    """
    knee = (ang["knee_L"] + ang["knee_R"]) / 2
    torso = abs(ang["torso_tilt"])
    s, fb = 100, []
    # Softened from <80 -> <60 (Escamilla 2001): parallel = interior 80, so <80 is NOT "too deep".
    if knee < 60: s -= 25; fb.append("\u8e72\u592a\u6df1, \u8d1f\u91cd\u65f6\u6ce8\u610f\u819d\u5173\u8282\u538b\u529b")
    elif knee > 150: s -= 30; fb.append("\u8e72\u4e0d\u591f\u6df1, \u5927\u817f\u8981\u5e73\u884c\u5730\u9762 (\u6807\u51c6\u2248\u819d80\u00b0)")
    elif knee > 130: s -= 10; fb.append("\u518d\u8e72\u6df1\u4e00\u70b9 (\u6807\u51c6\u2248\u819d80\u00b0)")
    if torso > 60: s -= 20; fb.append("\u8eaf\u5e72\u8fc7\u5ea6\u524d\u503e, \u6536\u7d27\u6838\u5fc3 (\u542f\u53d1\u5f0f\u9608\u503c)")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6!"

def _score_pushup(ang):
    """Push-up form score.

    Peer-reviewed evidence:
    - Dhahbi 2022 (Sports Biomech 21:1) standard bottom ~90 elbow flexion. https://pubmed.ncbi.nlm.nih.gov/30284496/
    - Polovinets 2026 (Handchir 58:243) deeper flexion raises elbow moment. https://pubmed.ncbi.nlm.nih.gov/42269686/
    """
    elb = (ang["elbow_L"] + ang["elbow_R"]) / 2
    s, fb = 100, []
    if elb > 150: s -= 30; fb.append("\u8098\u6ca1\u5f2f\u4e0b\u53bb, \u8981\u89e6\u5e95 (\u6807\u51c6\u2248\u809890\u00b0)")
    # Softened <70 -> <60 (Polovinets 2026): normal bottom ~90, <60 unusual.
    elif elb < 60: s -= 10; fb.append("\u8098\u5f2f\u592a\u591a, \u51cf\u5c11\u8155/\u8098\u8d1f\u8377")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6!"

def _score_plank(ang):
    """Plank form score.

    Peer-reviewed evidence:
    - Ekstrom 2007 (JOSPT 37:754) plank defined by straight body line. https://pubmed.ncbi.nlm.nih.gov/18560185/
    - Moreno-Navarro 2024 (JBMR 37:743) sag/pike shifts load to lumbar. https://pubmed.ncbi.nlm.nih.gov/38217576/
    """
    hip = (ang["hip_L"] + ang["hip_R"]) / 2
    s, fb = 100, []
    if hip < 160: s -= 25; fb.append("\u81c0\u90e8\u584c\u4e0b, \u4fdd\u6301\u4e00\u6761\u76f4\u7ebf (\u9acb\u2248180\u00b0)")
    elif hip > 200: s -= 25; fb.append("\u81c0\u90e8\u7fd8\u8d77(pike), \u4fdd\u6301\u4e00\u6761\u76f4\u7ebf (\u9acb\u2248180\u00b0)")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6!"

def _score_lunge(ang):
    """Lunge form score.

    Peer-reviewed evidence:
    - Escamilla 2008 (JOSPT 38:681) / 2022 (J Appl Biomech 38:210) front knee ~90 flexion.
      https://pubmed.ncbi.nlm.nih.gov/18978453/ ; https://pubmed.ncbi.nlm.nih.gov/35697336/
    - Hall 2015 (The Knee 22:506) clinically meaningful bilateral asymmetry down to ~4.
      https://pubmed.ncbi.nlm.nih.gov/25907262/
    """
    diff = abs(ang["knee_L"] - ang["knee_R"])
    s, fb = 100, []
    # 20 is a conservative floor for "genuine split stance" (clinical asymmetry ~4 is far smaller).
    if diff < 20: s -= 30; fb.append("\u4e24\u817f\u819d\u76d6\u5e94\u6709\u660e\u663e\u89d2\u5ea6\u5dee (\u5f13\u6b65\u5206\u817f)")
    front = min(ang["knee_L"], ang["knee_R"])
    if front > 110: s -= 15; fb.append("\u524d\u819d\u518d\u5f2f\u4e00\u70b9 (\u6807\u51c6\u2248\u524d\u819d90\u00b0)")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6!"

def _score_jack(ang):
    """Jumping-jack form score.

    Peer-reviewed evidence:
    - Lam & Bordoni 2023 (StatPearls NBK537148) full arm abduction 0-180.
      https://www.ncbi.nlm.nih.gov/books/NBK537148/

    Grading: peak = arms overhead (>=150); intermediate = 90-140; fail = <90.
    """
    sho = (ang["shoulder_L"] + ang["shoulder_R"]) / 2
    s, fb = 100, []
    if sho < 90:
        s -= 20; fb.append("\u624b\u8981\u4e3e\u8fc7\u5934\u9876 (\u80a9\u5916\u5c55\u2248180\u00b0)")
    elif sho < 140:
        s -= 8; fb.append("\u624b\u81c2\u672a\u5b8c\u5168\u8fc7\u5934 (\u6807\u51c6\u5e94\u2265150\u00b0)")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6!"


def _score_bicep_curl(ang):
    """Biceps-curl form score.

    Peer-reviewed evidence:
    - Pedrosa 2023 (Sports 11:39) curl ROM 0-135 flexion (interior 45-180).
      https://pubmed.ncbi.nlm.nih.gov/36828324/
    - Oliveira 2009 (JSSM 8:24) shoulder position changes biceps activation
      (shoulder-cheat degree cutoff is heuristic). https://pubmed.ncbi.nlm.nih.gov/24150552/
    """
    elb = (ang.get("elbow_L", 180) + ang.get("elbow_R", 180)) / 2
    sho = (ang.get("shoulder_L", 30) + ang.get("shoulder_R", 30)) / 2
    s, fb = 100, []
    if elb > 160: s -= 20; fb.append("\u624b\u81c2\u672a\u5f2f\u8d77, \u5b8c\u6574\u6536\u7f29 (\u6807\u51c6\u2248\u809845\u00b0)")
    # Softened <30 -> <45 (Pedrosa 2023 defines curl ROM 0-135, interior floor ~45).
    if elb < 45: s -= 15; fb.append("\u8098\u5f2f\u5f97\u592a\u8fc7, \u8d85\u51fa\u5e38\u89c4 curl ROM")
    if sho > 70: s -= 25; fb.append("\u80a9\u8180\u5728\u53d1\u529b, \u56fa\u5b9a\u80a9\u80db, \u53ea\u52a8\u8098 (\u542f\u53d1\u5f0f\u9608\u503c)")
    return max(0, s), "; ".join(fb) if fb else "\u6807\u51c6\u5f2f\u4e3e!"


def _score_shoulder_press(ang):
    """Shoulder-press form score.

    Peer-reviewed evidence:
    - Gundersen 2025 (Sports Biomech online) lockout: elbow ~170-180, shoulder ~150-180.
      https://pubmed.ncbi.nlm.nih.gov/41335596/
    - Russell 1989 (RQES 60:201) trunk deviation raises lumbar shear (30 back-arch = heuristic).
      https://pubmed.ncbi.nlm.nih.gov/2489844/

    Grading: fail if elbow<80 or shoulder<100; intermediate if elbow<140 or shoulder<150.
    """
    elb = (ang.get("elbow_L", 90) + ang.get("elbow_R", 90)) / 2
    sho = (ang.get("shoulder_L", 90) + ang.get("shoulder_R", 90)) / 2
    torso = abs(ang.get("torso_tilt", 0))
    s, fb = 100, []
    if elb < 80:
        s -= 30; fb.append("\u672a\u63a8\u5230\u4f4d, \u624b\u81c2\u8981\u5b8c\u5168\u4f38\u76f4 (\u9501\u5b9a\u2248\u8098170\u00b0+)")
    elif elb < 140:
        s -= 10; fb.append("\u672a\u5b8c\u5168\u9501\u5b9a (\u6807\u51c6\u2248\u8098170\u00b0+)")
    if sho < 100:
        s -= 20; fb.append("\u624b\u672a\u8fc7\u5934\u9876 (\u6807\u51c6\u2248\u80a9150\u00b0+)")
    elif sho < 150:
        s -= 8; fb.append("\u672a\u5b8c\u5168\u8fc7\u5934 (\u6807\u51c6\u2248\u80a9150\u00b0+)")
    if torso > 30:
        s -= 15; fb.append("\u8eaf\u5e72\u53cd\u5f13, \u80f8\u8154\u4e0d\u8981\u524d\u9876 (\u542f\u53d1\u5f0f\u9608\u503c)")
    return max(0, s), "; ".join(fb) if fb else "\u80a9\u63a8\u5230\u4f4d!"


'''


def main():
    raw = PATH.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")

    start = text.find("def _score_squat")
    end = text.find("FORM_RULES")
    if start < 0 or end < 0 or end <= start:
        raise SystemExit(f"markers not found: start={start} end={end}")
    new_text = text[:start] + NEW_BLOCK + text[end:]

    encoded = new_text.encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    PATH.write_bytes(encoded)
    print(f"patched {PATH} (bom_preserved={has_bom}, new_size={len(encoded)} bytes)")


if __name__ == "__main__":
    main()
