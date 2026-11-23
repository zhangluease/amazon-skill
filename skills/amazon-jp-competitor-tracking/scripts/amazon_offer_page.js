() => {
  // Evaluate on the open Amazon product tab using the browser tool's read-only evaluator.
  const clean = value => typeof value === 'string'
    ? value.replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u206f]/g, '').replace(/\s+/gu, ' ').trim().slice(0, 500) || null
    : null;
  const text = element => clean(element?.innerText || element?.textContent);
  const id = name => document.getElementById(name);
  const visible = element => Boolean(element && element.getClientRects().length);
  const postalCode = value => value?.match(/(?<!\d)[0-9]{3}-[0-9]{4}(?!\d)/)?.[0] || null;
  const priceArea = id('corePriceDisplay_desktop_feature_div') || id('corePrice_feature_div');
  const price = priceArea?.querySelector('.priceToPay') || id('priceblock_ourprice') || id('price_inside_buybox');
  const reference = priceArea?.querySelector('.basisPrice');
  const cart = ['add-to-cart-button', 'add-to-cart-button-ubb'].map(id).find(visible);
  const availability = id('availability');
  const destination = text(id('glow-ingress-line2'));
  const deliveryLink = id('contextualIngressPtLink');
  const url = new URL(location.href);
  const asin = url.pathname.match(/\/dp\/([A-Z0-9]{10})(?:\/|$)/i)?.[1]?.toUpperCase();
  const title = text(id('productTitle'));
  if (url.origin !== 'https://www.amazon.co.jp' || !asin || !title) return null;
  return {
    url: `https://www.amazon.co.jp/dp/${asin}`,
    observed_at: new Date().toISOString(),
    title,
    country_label: text(id('icp-touch-link-country')),
    destination_country_label: ['日本', '日本国', 'Japan'].includes(destination) ? destination : null,
    postal_code: postalCode(destination),
    offer_postal_code: postalCode(text(deliveryLink)) || postalCode(clean(deliveryLink?.getAttribute('aria-label'))),
    price_text: text(price?.querySelector('.a-offscreen')) || text(price),
    reference_text: text(reference),
    reference_price_text: text(reference?.querySelector('.a-offscreen')),
    discount_text: text(priceArea?.querySelector('.savingsPercentage')),
    cart: cart ? {
      label: clean(cart.value || cart.getAttribute('aria-label') || cart.getAttribute('title') || cart.innerText),
      enabled: !cart.matches(':disabled, [aria-disabled="true"]'),
    } : null,
    availability_text: visible(availability)
      ? text(availability.querySelector('span.a-color-success, span.a-color-price, span.a-color-state') || availability)
      : null,
  };
}
