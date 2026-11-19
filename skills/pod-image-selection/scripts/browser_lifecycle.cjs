// Only for browsers launched and owned by these scripts, never an attached user browser.
async function closeOwnedBrowser(browser) {
  if (!browser) return;
  let failure;
  try {
    for (const context of browser.contexts()) {
      try { await context.close(); }
      catch (error) { failure ||= error; }
    }
  } catch (error) { failure ||= error; }
  // A failed/disconnected context must not prevent the owned process from being cleaned up.
  try { await browser.close(); }
  catch (error) { failure ||= error; }
  if (failure) throw failure;
}

module.exports = { closeOwnedBrowser };
