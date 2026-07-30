/** OSRS icon helpers shared by Account, Achievements, and Merch. */
(function (global) {
  "use strict";

  var WIKI_IMG = "https://oldschool.runescape.wiki/images/";
  var ITEM_ICON_CDN = "https://static.runelite.net/cache/item/icon/";
  /* jsDelivr mirrors WOM app assets — wiseoldman.net itself is behind CF and
     blocks hotlinks from other origins. */
  var WOM_METRIC_IMG =
    "https://cdn.jsdelivr.net/gh/wise-old-man/wise-old-man@master/app/public/img/metrics/";

  /** Hiscores display order (skip overall in the skill grid). */
  var SKILL_ORDER = [
    "attack", "hitpoints", "mining",
    "strength", "agility", "smithing",
    "defence", "herblore", "fishing",
    "ranged", "thieving", "cooking",
    "prayer", "crafting", "firemaking",
    "magic", "fletching", "woodcutting",
    "runecrafting", "slayer", "farming",
    "construction", "hunter",
  ];

  var SKILL_LABEL = {
    attack: "Attack",
    hitpoints: "Hitpoints",
    mining: "Mining",
    strength: "Strength",
    agility: "Agility",
    smithing: "Smithing",
    defence: "Defence",
    herblore: "Herblore",
    fishing: "Fishing",
    ranged: "Ranged",
    thieving: "Thieving",
    cooking: "Cooking",
    prayer: "Prayer",
    crafting: "Crafting",
    firemaking: "Firemaking",
    magic: "Magic",
    fletching: "Fletching",
    woodcutting: "Woodcutting",
    runecrafting: "Runecraft",
    slayer: "Slayer",
    farming: "Farming",
    construction: "Construction",
    hunter: "Hunter",
    overall: "Overall",
  };

  /** Wiki detailed skill art for each metric (WOM key → image name). */
  var SKILL_ICON_FILE = {
    attack: "Attack.png",
    hitpoints: "Hitpoints.png",
    mining: "Mining.png",
    strength: "Strength.png",
    agility: "Agility.png",
    smithing: "Smithing.png",
    defence: "Defence.png",
    herblore: "Herblore.png",
    fishing: "Fishing.png",
    ranged: "Ranged.png",
    thieving: "Thieving.png",
    cooking: "Cooking.png",
    prayer: "Prayer.png",
    crafting: "Crafting.png",
    firemaking: "Firemaking.png",
    magic: "Magic.png",
    fletching: "Fletching.png",
    woodcutting: "Woodcutting.png",
    runecrafting: "Runecraft.png",
    slayer: "Slayer.png",
    farming: "Farming.png",
    construction: "Construction.png",
    hunter: "Hunter.png",
  };

  /** Boss hiscores display order (WOM Boss enum). */
  var BOSS_ORDER = [
    "abyssal_sire",
    "alchemical_hydra",
    "amoxliatl",
    "araxxor",
    "artio",
    "barrows_chests",
    "brutus",
    "bryophyta",
    "callisto",
    "calvarion",
    "cerberus",
    "chambers_of_xeric",
    "chambers_of_xeric_challenge_mode",
    "chaos_elemental",
    "chaos_fanatic",
    "commander_zilyana",
    "corporeal_beast",
    "crazy_archaeologist",
    "dagannoth_prime",
    "dagannoth_rex",
    "dagannoth_supreme",
    "deranged_archaeologist",
    "doom_of_mokhaiotl",
    "duke_sucellus",
    "general_graardor",
    "giant_mole",
    "grotesque_guardians",
    "hespori",
    "kalphite_queen",
    "king_black_dragon",
    "kraken",
    "kreearra",
    "kril_tsutsaroth",
    "lunar_chests",
    "maggot_king",
    "mimic",
    "nex",
    "nightmare",
    "phosanis_nightmare",
    "obor",
    "phantom_muspah",
    "sarachnis",
    "scorpia",
    "scurrius",
    "shellbane_gryphon",
    "skotizo",
    "sol_heredit",
    "spindel",
    "tempoross",
    "the_gauntlet",
    "the_corrupted_gauntlet",
    "the_hueycoatl",
    "the_leviathan",
    "the_royal_titans",
    "the_whisperer",
    "theatre_of_blood",
    "theatre_of_blood_hard_mode",
    "thermonuclear_smoke_devil",
    "tombs_of_amascut",
    "tombs_of_amascut_expert",
    "tzkal_zuk",
    "tztok_jad",
    "vardorvis",
    "venenatis",
    "vetion",
    "vorkath",
    "wintertodt",
    "yama",
    "zalcano",
    "zulrah",
  ];

  var BOSS_LABEL = {
    abyssal_sire: "Abyssal Sire",
    alchemical_hydra: "Alchemical Hydra",
    amoxliatl: "Amoxliatl",
    araxxor: "Araxxor",
    artio: "Artio",
    barrows_chests: "Barrows Chests",
    brutus: "Brutus",
    bryophyta: "Bryophyta",
    callisto: "Callisto",
    calvarion: "Calvar'ion",
    cerberus: "Cerberus",
    chambers_of_xeric: "Chambers of Xeric",
    chambers_of_xeric_challenge_mode: "Chambers of Xeric: CM",
    chaos_elemental: "Chaos Elemental",
    chaos_fanatic: "Chaos Fanatic",
    commander_zilyana: "Commander Zilyana",
    corporeal_beast: "Corporeal Beast",
    crazy_archaeologist: "Crazy Archaeologist",
    dagannoth_prime: "Dagannoth Prime",
    dagannoth_rex: "Dagannoth Rex",
    dagannoth_supreme: "Dagannoth Supreme",
    deranged_archaeologist: "Deranged Archaeologist",
    doom_of_mokhaiotl: "Doom of Mokhaiotl",
    duke_sucellus: "Duke Sucellus",
    general_graardor: "General Graardor",
    giant_mole: "Giant Mole",
    grotesque_guardians: "Grotesque Guardians",
    hespori: "Hespori",
    kalphite_queen: "Kalphite Queen",
    king_black_dragon: "King Black Dragon",
    kraken: "Kraken",
    kreearra: "Kree'arra",
    kril_tsutsaroth: "K'ril Tsutsaroth",
    lunar_chests: "Lunar Chests",
    maggot_king: "Maggot King",
    mimic: "Mimic",
    nex: "Nex",
    nightmare: "Nightmare",
    phosanis_nightmare: "Phosani's Nightmare",
    obor: "Obor",
    phantom_muspah: "Phantom Muspah",
    sarachnis: "Sarachnis",
    scorpia: "Scorpia",
    scurrius: "Scurrius",
    shellbane_gryphon: "Shellbane Gryphon",
    skotizo: "Skotizo",
    sol_heredit: "Sol Heredit",
    spindel: "Spindel",
    tempoross: "Tempoross",
    the_gauntlet: "The Gauntlet",
    the_corrupted_gauntlet: "Corrupted Gauntlet",
    the_hueycoatl: "The Hueycoatl",
    the_leviathan: "The Leviathan",
    the_royal_titans: "The Royal Titans",
    the_whisperer: "The Whisperer",
    theatre_of_blood: "Theatre of Blood",
    theatre_of_blood_hard_mode: "Theatre of Blood: HM",
    thermonuclear_smoke_devil: "Thermonuclear Smoke Devil",
    tombs_of_amascut: "Tombs of Amascut",
    tombs_of_amascut_expert: "Tombs of Amascut: Expert",
    tzkal_zuk: "TzKal-Zuk",
    tztok_jad: "TzTok-Jad",
    vardorvis: "Vardorvis",
    venenatis: "Venenatis",
    vetion: "Vet'ion",
    vorkath: "Vorkath",
    wintertodt: "Wintertodt",
    yama: "Yama",
    zalcano: "Zalcano",
    zulrah: "Zulrah",
  };

  var TYPE_BADGE = {
    ironman: "Ironman_chat_badge.png",
    hardcore: "Hardcore_ironman_chat_badge.png",
    ultimate: "Ultimate_ironman_chat_badge.png",
  };

  function skillIconUrl(metric) {
    var file = SKILL_ICON_FILE[metric];
    return file ? WIKI_IMG + file : null;
  }

  function bossIconUrl(metric) {
    if (!metric) return null;
    return WOM_METRIC_IMG + encodeURIComponent(metric) + ".png";
  }

  function combatIconUrl() {
    return WIKI_IMG + "Combat.png";
  }

  function typeBadgeUrl(playerType) {
    var file = TYPE_BADGE[playerType];
    return file ? WIKI_IMG + file : null;
  }

  function skillLabel(metric) {
    return SKILL_LABEL[metric] || metric;
  }

  function bossLabel(metric) {
    return BOSS_LABEL[metric] || metric;
  }

  /** RuneLite inventory icon CDN URL for a GE item id. */
  function itemIconUrl(itemId) {
    if (itemId == null || itemId === "") return null;
    return ITEM_ICON_CDN + itemId + ".png";
  }

  global.OsrsIcons = {
    SKILL_ORDER: SKILL_ORDER,
    BOSS_ORDER: BOSS_ORDER,
    skillIconUrl: skillIconUrl,
    bossIconUrl: bossIconUrl,
    combatIconUrl: combatIconUrl,
    typeBadgeUrl: typeBadgeUrl,
    skillLabel: skillLabel,
    bossLabel: bossLabel,
    itemIconUrl: itemIconUrl,
  };
})(typeof window !== "undefined" ? window : globalThis);
