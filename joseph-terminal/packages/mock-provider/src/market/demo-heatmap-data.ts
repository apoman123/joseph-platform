/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Fictional DEMO heatmap universe: companies, funds and coins.
 *
 * Every name and ticker here is invented for the DEMO and does not describe
 * a real security. A few rows point at the existing `mock` venue
 * instruments so a click opens a DEMO chart.
 */

/** A fictional listed company. */
export interface DemoCompany {
  /** Ticker or code. */
  symbol: string;
  /** Company name. */
  name: string;
  /** Sector. */
  sector: string;
  /** Industry within the sector. */
  industry: string;
  /** Listing exchange: Nasdaq, NYSE, TWSE or TPEx. */
  exchange: string;
  /** Market capitalization in billions of the quote currency. */
  capB: number;
  /** Reference price. */
  price: number;
  /** DEMO instrument to chart, if one exists. */
  instrumentId?: string;
}

/** A fictional exchange-traded fund. */
export interface DemoFund {
  /** Ticker or code. */
  symbol: string;
  /** Fund name. */
  name: string;
  /** Asset class. */
  assetClass: string;
  /** Listing exchange. */
  exchange: string;
  /** Typical daily turnover in millions of the quote currency. */
  turnoverM: number;
  /** Reference price. */
  price: number;
  /** DEMO instrument to chart, if one exists. */
  instrumentId?: string;
}

/** A fictional coin. */
export interface DemoCoin {
  /** Ticker. */
  symbol: string;
  /** Coin name. */
  name: string;
  /** Market capitalization in billions of USD. */
  capB: number;
  /** Reference price in USD. */
  price: number;
  /** Category tags: `stablecoin`, `defi`. */
  tags?: readonly string[];
  /** DEMO instrument to chart, if one exists. */
  instrumentId?: string;
}

/**
 * Builds a company row compactly.
 * @param symbol Ticker.
 * @param name Name.
 * @param sector Sector.
 * @param industry Industry.
 * @param exchange Exchange.
 * @param capB Market cap in billions.
 * @param price Reference price.
 * @return Company row.
 */
function co(
  symbol: string,
  name: string,
  sector: string,
  industry: string,
  exchange: string,
  capB: number,
  price: number
): DemoCompany {
  return {symbol, name, sector, industry, exchange, capB, price};
}

const T = 'Technology';
const TEL = 'Telecommunications';
const CD = 'Consumer Discretionary';
const HC = 'Health Care';
const FIN = 'Finance';
const IND = 'Industrials';
const EN = 'Energy';
const UT = 'Utilities';
const RE = 'Real Estate';
const CS = 'Consumer Staples';
const BM = 'Basic Materials';
const N = 'Nasdaq';
const Y = 'NYSE';

/** Fictional US companies (Nasdaq and NYSE listings). */
export const DEMO_US_COMPANIES: readonly DemoCompany[] = [
  co('ORBQ', 'Orbitron Semiconductor', T, 'Semiconductors', N, 1450, 412.3),
  co('QLXD', 'Quantalux Devices', T, 'Semiconductors', N, 620, 188.4),
  co('FRNG', 'Ferngate Micro', T, 'Semiconductors', N, 180, 64.2),
  co('NIMB', 'Nimbara Software', T, 'Software', N, 980, 355.1),
  co('KODX', 'Kodexa Systems', T, 'Software', N, 410, 221.7),
  co('LUMQ', 'Lumiq Analytics', T, 'Software', N, 95, 48.9),
  co('SYLV', 'Sylvantis Cloud', T, 'Software', N, 140, 77.5),
  co('PRSM', 'Prismora Computing', T, 'Computer Hardware', N, 1210, 268.0),
  co('HALX', 'Halcyx Storage', T, 'Computer Hardware', Y, 72, 39.6),
  co('VERQ', 'Verquin Consulting', T, 'IT Services', Y, 160, 142.8),
  co('CRSW', 'Crestwave Mobile', TEL, 'Wireless', Y, 230, 47.1),
  co('TALN', 'Talonet Telecom', TEL, 'Wireless', Y, 88, 21.4),
  co('MRDN', 'Meridane Media', TEL, 'Media', N, 310, 96.3),
  co('PIXV', 'Pixvale Studios', TEL, 'Media', N, 54, 33.2),
  co('BZRX', 'Bazaro Marketplace', CD, 'Internet Retail', N, 1020, 188.6),
  co('CRTQ', 'Cartiq Commerce', CD, 'Internet Retail', N, 64, 52.0),
  co('VTRX', 'Voltrix Motors', CD, 'Automobiles', N, 560, 241.9),
  co('RDGL', 'Ridgeline Auto Parts', CD, 'Automobiles', Y, 38, 71.3),
  co('SAVR', 'Savora Kitchens', CD, 'Restaurants', Y, 190, 284.4),
  co('BRWL', 'Brewell Coffee', CD, 'Restaurants', N, 82, 96.0),
  co('STRD', 'Striderun Apparel', CD, 'Apparel', Y, 120, 101.2),
  co('CURX', 'Curexa Pharma', HC, 'Pharmaceuticals', Y, 480, 133.5),
  co('VTLN', 'Vitalune Therapeutics', HC, 'Pharmaceuticals', Y, 300, 412.0),
  co('PLMB', 'Palomba Labs', HC, 'Pharmaceuticals', N, 56, 27.8),
  co('GENQ', 'Genquill Bio', HC, 'Biotechnology', N, 150, 291.1),
  co('HLXB', 'Helixbury Genomics', HC, 'Biotechnology', N, 44, 63.4),
  co('CRDQ', 'Cardiq Medical', HC, 'Medical Devices', Y, 210, 168.2),
  co('OSTV', 'Osteva Implants', HC, 'Medical Devices', Y, 61, 92.7),
  co('AEGC', 'Aegicare Health', HC, 'Managed Care', Y, 430, 455.6),
  co('GRNB', 'Granite Bancorp', FIN, 'Banks', Y, 520, 188.9),
  co('HRBR', 'Harborline Bank', FIN, 'Banks', Y, 140, 61.0),
  co('TRVB', 'Trivale Financial', FIN, 'Banks', Y, 66, 44.3),
  co('SHLW', 'Shieldwell Insurance', FIN, 'Insurance', Y, 260, 312.5),
  co('ANKV', 'Ankerva Re', FIN, 'Insurance', Y, 90, 147.1),
  co('SUMV', 'Summitvale Capital', FIN, 'Asset Management', Y, 180, 912.4),
  co('QRYX', 'Quarryx Payments', FIN, 'Payments', Y, 620, 288.0),
  co('TKRD', 'Tickerdale Exchange', FIN, 'Payments', N, 95, 211.6),
  co('AVNQ', 'Avionique Aero', IND, 'Aerospace', Y, 270, 233.4),
  co('JTRV', 'Jetrove Systems', IND, 'Aerospace', Y, 75, 118.0),
  co('HMRK', 'Hammerok Machinery', IND, 'Machinery', Y, 160, 341.2),
  co('TRCV', 'Tractiva Equipment', IND, 'Machinery', Y, 48, 86.5),
  co('CRGX', 'Cargexa Logistics', IND, 'Freight', Y, 130, 214.8),
  co('RLVN', 'Railvane Freight', IND, 'Freight', Y, 104, 236.1),
  {
    ...co(
      'ACME',
      'Acme Corporation (demo)',
      IND,
      'Conglomerates',
      Y,
      140,
      182.4
    ),
    instrumentId: 'mock:stock:ACME',
  },
  co('PTRV', 'Petrovane Energy', EN, 'Oil & Gas', Y, 430, 112.7),
  co('DRLQ', 'Drillquest Services', EN, 'Oil & Gas', Y, 52, 37.4),
  co('GSPK', 'Gaspeak Midstream', EN, 'Pipelines', Y, 70, 29.9),
  co('SOLQ', 'Solquist Power', EN, 'Renewables', N, 39, 58.2),
  co('AMPV', 'Ampervale Electric', UT, 'Electric Utilities', Y, 150, 74.1),
  co('GRDN', 'Gridnorth Utilities', UT, 'Electric Utilities', Y, 82, 61.5),
  co('AQVR', 'Aquaverde Water', UT, 'Water Utilities', Y, 31, 132.0),
  co('TWRX', 'Towerix Realty', RE, 'Specialty REITs', Y, 105, 211.3),
  co('LFTQ', 'Loftquay Properties', RE, 'Residential REITs', Y, 48, 77.0),
  co('DPTV', 'Depotvale Logistics REIT', RE, 'Industrial REITs', Y, 112, 121.4),
  co('FZZQ', 'Fizzquell Beverages', CS, 'Beverages', Y, 260, 61.2),
  co('HVST', 'Harvestine Foods', CS, 'Packaged Foods', Y, 120, 59.3),
  co('CLNV', 'Cleanova Home', CS, 'Household Products', Y, 330, 158.6),
  co('GRCV', 'Grocervale Stores', CS, 'Retail Staples', N, 410, 802.1),
  co('CHMV', 'Chemvista Materials', BM, 'Chemicals', Y, 88, 104.4),
  co('LTHQ', 'Lithquarry Mining', BM, 'Metals & Mining', Y, 41, 66.0),
  co('STLV', 'Steelvane Works', BM, 'Metals & Mining', Y, 34, 51.7),
];

/** Fictional Nasdaq listings in the DEMO "Nasdaq 100". */
export const DEMO_NASDAQ_100: ReadonlySet<string> = new Set(
  DEMO_US_COMPANIES.filter(row => row.exchange === N && row.capB >= 60).map(
    row => row.symbol
  )
);

const SEMI = 'Semiconductor 半導體業';
const PARTS = 'Electronic Components 電子零組件業';
const OPTO = 'Optoelectronic 光電業';
const FINTW = 'Financial & Insurance 金融保險業';
const SHIP = 'Shipping 航運業';
const PLAS = 'Plastics 塑膠工業';
const COMM = 'Communications 通信網路業';
const BIO = 'Biotech & Medical 生技醫療業';
const COMP = 'Computer & Peripheral 電腦及週邊設備業';

/** Fictional Taiwan companies (TWSE and TPEx), single-level industries. */
export const DEMO_TW_COMPANIES: readonly DemoCompany[] = [
  {
    ...co('9901', 'Demo Semiconductor (demo)', SEMI, '', 'TWSE', 15100, 585),
    instrumentId: 'mock:stock:9901',
  },
  co('D101', 'Demo Wafer Tools', SEMI, '', 'TWSE', 2100, 312),
  co('D102', 'Demo Logic Design', SEMI, '', 'TWSE', 1650, 1180),
  co('D103', 'Demo Precision Parts', PARTS, '', 'TWSE', 980, 144),
  co('D104', 'Demo Connector Works', PARTS, '', 'TWSE', 410, 88.5),
  co('D105', 'Demo Panel Optics', OPTO, '', 'TWSE', 520, 23.4),
  co('D106', 'Demo Lens Craft', OPTO, '', 'TWSE', 640, 2450),
  co('D107', 'Demo Financial Holdings', FINTW, '', 'TWSE', 1320, 36.2),
  co('D108', 'Demo Life Insurance', FINTW, '', 'TWSE', 870, 61.8),
  co('D109', 'Demo Container Lines', SHIP, '', 'TWSE', 590, 182),
  co('D110', 'Demo Petrochemical', PLAS, '', 'TWSE', 450, 47.3),
  co('D111', 'Demo Telecom', COMM, '', 'TWSE', 960, 128),
  co('D112', 'Demo Server Systems', COMP, '', 'TWSE', 1480, 296),
  co('D201', 'Demo IC Packaging', SEMI, '', 'TPEx', 380, 512),
  co('D202', 'Demo Power Chips', SEMI, '', 'TPEx', 210, 158),
  co('D203', 'Demo Bio Pharma', BIO, '', 'TPEx', 160, 74.6),
  co('D204', 'Demo Medical Devices', BIO, '', 'TPEx', 95, 203),
  co('D205', 'Demo Network Gear', COMM, '', 'TPEx', 120, 86.1),
  co('D206', 'Demo Thermal Parts', PARTS, '', 'TPEx', 140, 341),
  co('D207', 'Demo Game Studio', COMP, '', 'TPEx', 110, 465),
];

/**
 * Builds a fund row compactly.
 * @param symbol Ticker.
 * @param name Name.
 * @param assetClass Asset class.
 * @param exchange Exchange.
 * @param turnoverM Daily turnover in millions.
 * @param price Reference price.
 * @return Fund row.
 */
function fund(
  symbol: string,
  name: string,
  assetClass: string,
  exchange: string,
  turnoverM: number,
  price: number
): DemoFund {
  return {symbol, name, assetClass, exchange, turnoverM, price};
}

const EQ = 'Equity';
const FI = 'Fixed income';
const CM = 'Commodity';
const CU = 'Currency';
const CR = 'Crypto';
const MA = 'Multi-asset/Alternative';

/** Fictional US ETFs. */
export const DEMO_US_FUNDS: readonly DemoFund[] = [
  fund('VXMK', 'Demo Total Market Equity ETF', EQ, 'NYSE Arca', 9800, 512.4),
  fund('BRDM', 'Demo Broad 500 Index ETF', EQ, 'NYSE Arca', 24500, 598.1),
  fund('GRWX', 'Demo Growth Leaders ETF', EQ, 'Nasdaq', 7200, 311.6),
  fund('DIVQ', 'Demo Dividend Equity ETF', EQ, 'NYSE Arca', 1400, 84.2),
  fund('SMCQ', 'Demo Small Cap Equity ETF', EQ, 'NYSE Arca', 3100, 221.0),
  fund('INTQ', 'Demo International Equity ETF', EQ, 'NYSE Arca', 1900, 52.7),
  fund('EMKQ', 'Demo Emerging Markets ETF', EQ, 'NYSE Arca', 1600, 44.9),
  fund('TECQ', 'Demo Technology Sector ETF', EQ, 'NYSE Arca', 2700, 236.5),
  fund('HLTQ', 'Demo Health Care Sector ETF', EQ, 'NYSE Arca', 900, 146.3),
  {
    ...fund(
      'TLTD',
      'Demo 20+ Year Treasury Bond ETF',
      FI,
      'Nasdaq',
      2300,
      91.2
    ),
    instrumentId: 'mock:etf:TLTD',
  },
  fund('BNDQ', 'Demo Aggregate Bond ETF', FI, 'Nasdaq', 1200, 72.8),
  fund('CRPQ', 'Demo Corporate Bond ETF', FI, 'NYSE Arca', 800, 108.4),
  fund('MUNQ', 'Demo Municipal Bond ETF', FI, 'NYSE Arca', 300, 106.9),
  fund(
    'TIPQ',
    'Demo Inflation-Protected Bond ETF',
    FI,
    'NYSE Arca',
    260,
    109.7
  ),
  fund('GLDQ', 'Demo Gold Trust', CM, 'NYSE Arca', 2100, 241.3),
  fund('SLVQ', 'Demo Silver Trust', CM, 'NYSE Arca', 600, 28.6),
  fund('OILQ', 'Demo Crude Oil Fund', CM, 'NYSE Arca', 450, 71.2),
  fund('AGRQ', 'Demo Agriculture Commodity Fund', CM, 'NYSE Arca', 60, 24.8),
  fund('USDQ', 'Demo US Dollar Index Fund', CU, 'NYSE Arca', 140, 28.1),
  fund('EURQ', 'Demo Euro Currency Trust', CU, 'NYSE Arca', 40, 98.6),
  fund('BTCQ', 'Demo Bitcoin Trust ETF', CR, 'Nasdaq', 1800, 52.4),
  fund('ETHQ', 'Demo Ether ETF', CR, 'Nasdaq', 420, 24.9),
  fund('ALCQ', 'Demo Global Allocation ETF', MA, 'NYSE Arca', 90, 39.7),
];

/** Fictional Taiwan ETFs. */
export const DEMO_TW_FUNDS: readonly DemoFund[] = [
  fund('D501', 'Demo Taiwan 50 ETF 示範台灣50', EQ, 'TWSE', 9200, 188.4),
  fund('D502', 'Demo High Dividend ETF 示範高股息', EQ, 'TWSE', 6100, 37.6),
  fund('D503', 'Demo Semiconductor ETF 示範半導體', EQ, 'TWSE', 2400, 29.3),
  {
    ...fund(
      '00999B',
      'Demo US Treasury 20+ Year Bond ETF 示範美債20年',
      FI,
      'TPEx',
      3300,
      26.2
    ),
    instrumentId: 'mock:etf:00999B',
  },
  fund(
    'D504',
    'Demo Investment Grade Bond ETF 示範公司債',
    FI,
    'TPEx',
    1100,
    34.1
  ),
  fund('D505', 'Demo Gold ETF 示範黃金', CM, 'TWSE', 380, 41.7),
  fund('D506', 'Demo Crude Oil ETF 示範原油', CM, 'TWSE', 260, 18.2),
  fund('D507', 'Demo US Dollar ETF 示範美元', CU, 'TWSE', 90, 21.4),
  fund(
    'D508',
    'Demo Multi-Asset Income ETF 示範多重資產',
    MA,
    'TWSE',
    140,
    15.6
  ),
];

/**
 * Builds a coin row compactly.
 * @param symbol Ticker.
 * @param name Name.
 * @param capB Market cap in billions of USD.
 * @param price Price in USD.
 * @param tags Category tags.
 * @return Coin row.
 */
function coin(
  symbol: string,
  name: string,
  capB: number,
  price: number,
  tags: readonly string[] = []
): DemoCoin {
  return {symbol, name, capB, price, tags};
}

/** Fictional coins plus DEMO stand-ins for the three mock perpetuals. */
export const DEMO_COINS: readonly DemoCoin[] = [
  {
    ...coin('BTC', 'Bitcoin (demo)', 1880, 95000),
    instrumentId: 'mock:perp:BTCUSD',
  },
  {...coin('ETH', 'Ether (demo)', 410, 3400), instrumentId: 'mock:perp:ETHUSD'},
  {...coin('SOL', 'Solana (demo)', 86, 180), instrumentId: 'mock:perp:SOLUSD'},
  coin('DUSD', 'Demo Dollar', 140, 1, ['stablecoin']),
  coin('QUSD', 'Quillcoin USD', 62, 1, ['stablecoin']),
  coin('VUSD', 'Vaultmark Dollar', 9, 1, ['stablecoin', 'defi']),
  coin('ZNTH', 'Zenthra', 74, 2.41),
  coin('KVLR', 'Kavelar', 52, 0.62),
  coin('PLSM', 'Plasmyr', 38, 7.85),
  coin('VYRN', 'Vyrnet', 31, 0.184),
  coin('ARKQ', 'Arkquill', 24, 18.6),
  coin('MOTE', 'Motelith', 19, 4.12),
  coin('GLYX', 'Glyxon', 16, 0.0912),
  coin('TRVN', 'Trevana', 14, 11.3),
  coin('HXON', 'Hexonite', 12, 0.73),
  coin('BRZL', 'Breezle', 10, 0.00042),
  coin('QNTX', 'Quintex Chain', 9.4, 26.8),
  coin('ORBL', 'Orbleaf', 8.1, 3.36),
  coin('SNPR', 'Sniperio', 7.2, 0.155),
  coin('FLXR', 'Fluxara', 6.5, 1.92),
  coin('NOVQ', 'Novaquill', 5.8, 42.5),
  coin('RIVX', 'Rivexa', 5.1, 0.088),
  coin('TALQ', 'Talquin', 4.4, 6.71),
  coin('MYRX', 'Myrexa', 3.9, 0.318),
  coin('CELQ', 'Celquar', 3.3, 15.2),
  coin('DRFT', 'Driftwave', 2.8, 0.0071),
  coin('KRNL', 'Kernelite', 2.2, 2.05),
  coin('PYRA', 'Pyramint', 1.7, 0.47),
  coin('LQDX', 'Liquidex', 21, 9.87, ['defi']),
  coin('SWPR', 'Swapora', 13, 6.42, ['defi']),
  coin('LNDQ', 'Lendquill', 11, 214.0, ['defi']),
  coin('YLDV', 'Yieldvane', 6.9, 1.08, ['defi']),
  coin('DXOR', 'Dexora', 5.4, 0.91, ['defi']),
  coin('STKQ', 'Stakeqo', 4.8, 31.6, ['defi']),
  coin('BRDG', 'Bridgemint', 3.6, 0.264, ['defi']),
  coin('ORCV', 'Oraclevane', 3.1, 12.9, ['defi']),
  coin('PERP', 'Perpetra', 2.6, 0.733, ['defi']),
  coin('VLTQ', 'Vaultiq', 1.9, 3.48, ['defi']),
  coin('MNTQ', 'Mintquay', 1.4, 0.0533, ['defi']),
  coin('SYNQ', 'Synquest', 1.1, 0.389, ['defi']),
];
