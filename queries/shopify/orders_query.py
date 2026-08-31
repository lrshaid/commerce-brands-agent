"""Shopify orders bulk-operation query recovered from the source photos.

This is the broad source selector.  The registered ``orders_query.graphql``
remains the smaller, schema-checked runtime snapshot until this selector is
validated against the configured Admin API version.
"""

from datetime import datetime, timezone
from typing import Optional


_MONEY_BAG = """{{ shopMoney {{ amount currencyCode }} presentmentMoney {{ amount currencyCode }} }}"""


# Braces are doubled because ``build_orders_bulk_query`` fills this template
# with ``str.format``.  ``{query_clause}`` is the only replacement field.
ORDERS_BULK_QUERY = """
{{
  orders{query_clause} {{
    edges {{
      node {{
        __typename
        id
        name
        note
        tags
        test
        email
        phone
        number
        confirmed
        closedAt
        cancelledAt
        cancelReason
        createdAt
        updatedAt
        processedAt
        currencyCode
        presentmentCurrencyCode
        displayFinancialStatus
        displayFulfillmentStatus
        taxExempt
        taxesIncluded
        dutiesIncluded
        estimatedTaxes
        customerAcceptsMarketing
        customerLocale
        confirmationNumber
        sourceName
        sourceIdentifier
        registeredSourceUrl
        statusPageUrl
        totalWeight
        poNumber
        clientIp
        paymentGatewayNames
        discountCodes
        totalPriceSet __MONEY_BAG__
        subtotalPriceSet __MONEY_BAG__
        totalTaxSet __MONEY_BAG__
        totalDiscountsSet __MONEY_BAG__
        totalShippingPriceSet __MONEY_BAG__
        totalOutstandingSet __MONEY_BAG__
        totalTipReceivedSet __MONEY_BAG__
        currentSubtotalPriceSet __MONEY_BAG__
        currentTotalPriceSet __MONEY_BAG__
        currentTotalTaxSet __MONEY_BAG__
        currentTotalDiscountsSet __MONEY_BAG__
        currentTotalAdditionalFeesSet __MONEY_BAG__
        originalTotalAdditionalFeesSet __MONEY_BAG__
        totalCashRoundingAdjustment {{
          paymentSet __MONEY_BAG__
          refundSet __MONEY_BAG__
        }}
        app {{ id name }}
        staffMember {{ id }}
        physicalLocation {{ id name }}
        merchantOfRecordApp {{ id }}
        merchantBusinessEntity {{ id }}
        customer {{
          id
          email
          firstName
          lastName
          phone
          createdAt
          updatedAt
          verifiedEmail
          tags
          state
          note
          taxExempt
          taxExemptions
          multipassIdentifier
          defaultEmailAddress {{
            marketingState
            marketingOptInLevel
            marketingUpdatedAt
          }}
          defaultPhoneNumber {{
            marketingState
            marketingOptInLevel
            marketingUpdatedAt
            marketingCollectedFrom
          }}
          defaultAddress {{
            id address1 address2 city province provinceCode zip country
            countryCodeV2 name firstName lastName company phone latitude longitude
          }}
        }}
        billingAddress {{
          address1 address2 city province provinceCode zip country countryCodeV2
          name firstName lastName company phone latitude longitude
        }}
        shippingAddress {{
          address1 address2 city province provinceCode zip country countryCodeV2
          name firstName lastName company phone latitude longitude
        }}
        customerJourneySummary {{ lastVisit {{ landingPage referrerUrl }} }}
        customAttributes {{ key value }}
        paymentTerms {{ id paymentTermsName }}
        taxLines {{
          title rate ratePercentage priceSet __MONEY_BAG__ channelLiable
        }}
        fulfillments {{
          id name status displayStatus createdAt updatedAt
          location {{ id }}
          service {{ handle }}
          trackingInfo {{ company number url }}
        }}
        refunds {{
          id createdAt processedAt note
          staffMember {{ id }}
          totalRefundedSet __MONEY_BAG__
          duties {{ amountSet {{ shopMoney {{ amount currencyCode }} }} }}
        }}
        lineItems {{
          edges {{
            node {{
              __typename id sku name title variantTitle quantity vendor
              requiresShipping taxable currentQuantity isGiftCard fulfillableQuantity
              fulfillmentStatus
              fulfillmentService {{ handle serviceName type }}
              originalUnitPriceSet __MONEY_BAG__
              discountedUnitPriceSet __MONEY_BAG__
              totalDiscountSet __MONEY_BAG__
              product {{ id }}
              variant {{ id inventoryItem {{ tracked }} }}
              staffMember {{ id }}
              customAttributes {{ key value }}
              discountAllocations {{
                allocatedAmountSet __MONEY_BAG__
                discountApplication {{
                  index targetType allocationMethod targetSelection
                }}
              }}
              taxLines {{
                title rate ratePercentage priceSet __MONEY_BAG__ channelLiable
              }}
            }}
          }}
        }}
        shippingLines {{
          edges {{
            node {{
              __typename id title code source carrierIdentifier
              originalPriceSet __MONEY_BAG__
              discountedPriceSet __MONEY_BAG__
              discountAllocations {{
                allocatedAmountSet __MONEY_BAG__
                discountApplication {{ index }}
              }}
              taxLines {{
                title rate ratePercentage priceSet __MONEY_BAG__ channelLiable
              }}
            }}
          }}
        }}
        discountApplications {{
          edges {{
            node {{
              __typename index allocationMethod targetSelection targetType
              value {{
                __typename
                ... on MoneyV2 {{ amount currencyCode }}
                ... on PricingPercentageValue {{ percentage }}
              }}
              ... on AutomaticDiscountApplication {{ title }}
              ... on DiscountCodeApplication {{ code }}
              ... on ManualDiscountApplication {{ title description }}
              ... on ScriptDiscountApplication {{ title }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

ORDERS_BULK_QUERY = ORDERS_BULK_QUERY.replace("__MONEY_BAG__", _MONEY_BAG)


def build_orders_bulk_query(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> str:
    """Build the orders bulk query with an optional ``updated_at`` window.

    Both bounds produce a half-open interval; a lone start is open-ended; and
    two ``None`` values enumerate the complete order catalog.
    """
    if start is None and end is None:
        query_clause = ""
    else:
        parts: list[str] = []
        if start is not None:
            parts.append(f"updated_at:>='{_format_iso(start)}'")
        if end is not None:
            parts.append(f"updated_at:<'{_format_iso(end)}'")
        joined = " AND ".join(parts)
        query_clause = f'(query: "{joined}")'
    return ORDERS_BULK_QUERY.format(query_clause=query_clause)


def _format_iso(ts: datetime) -> str:
    """Emit a UTC ISO-8601 timestamp accepted by Shopify search syntax."""
    if ts.tzinfo is None:
        raise ValueError(
            "Refusing to emit a naive timestamp into the Shopify 'query:' "
            f"clause: {ts!r}. Pass a timezone-aware datetime (UTC preferred)."
        )
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
