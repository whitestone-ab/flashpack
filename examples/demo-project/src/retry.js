class RetryPolicy {
  constructor({ attempts = 3, baseDelayMs = 100 } = {}) {
    this.attempts = attempts;
    this.baseDelayMs = baseDelayMs;
  }

  delayFor(attempt) {
    return this.baseDelayMs * 2 ** attempt;
  }

  async run(task) {
    let lastError;
    for (let attempt = 0; attempt < this.attempts; attempt += 1) {
      try {
        return await task();
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => setTimeout(resolve, this.delayFor(attempt)));
      }
    }
    throw lastError;
  }
}

module.exports = { RetryPolicy };
