/** One loaded scrape job. Missing timing fields mean the collector supplied no value. */
export class ScrapeConfig {
  readonly jobName: string;
  readonly scrapeInterval?: string;
  readonly scrapeTimeout?: string;
  readonly metricsPath?: string;
  readonly scheme?: string;

  constructor(config: ScrapeConfig) {
    this.jobName = config.jobName;
    this.scrapeInterval = config.scrapeInterval;
    this.scrapeTimeout = config.scrapeTimeout;
    this.metricsPath = config.metricsPath;
    this.scheme = config.scheme;
  }
}

/** One discovered target. Dropped targets can have only discovery labels. */
export class ScrapeTarget {
  readonly state: "active" | "dropped";
  readonly discoveredLabels?: Readonly<Record<string, string>>;
  readonly labels?: Readonly<Record<string, string>>;
  readonly scrapePool?: string;
  readonly scrapeUrl?: string;
  readonly health?: string;
  readonly lastError?: string;
  readonly lastScrape?: string;
  /** Duration of the last scrape in seconds. */
  readonly lastScrapeDuration?: number;
  readonly scrapeInterval?: string;
  readonly scrapeTimeout?: string;

  constructor(target: ScrapeTarget) {
    this.state = target.state;
    this.discoveredLabels = target.discoveredLabels;
    this.labels = target.labels;
    this.scrapePool = target.scrapePool;
    this.scrapeUrl = target.scrapeUrl;
    this.health = target.health;
    this.lastError = target.lastError;
    this.lastScrape = target.lastScrape;
    this.lastScrapeDuration = target.lastScrapeDuration;
    this.scrapeInterval = target.scrapeInterval;
    this.scrapeTimeout = target.scrapeTimeout;
  }
}
