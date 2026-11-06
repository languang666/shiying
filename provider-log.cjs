// The upstream service logs temporary tokens; keep operational logs only.
console.debug = () => {};
for (const level of ['log', 'info', 'warn', 'error']) {
  const original = console[level].bind(console);
  console[level] = (...args) => {
    const text = args.map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' ');
    original(/potoken|integritytoken|mintrefreshthreshold/i.test(text) ? '[token details redacted]' : text);
  };
}
