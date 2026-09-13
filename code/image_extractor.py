"""
code/image_extractor.py

Dedicated resolution layer for recovering the 16 missing financial_events.amount values
from corresponding receipts, payslips, and invoices in dataset/media/images/.
Strictly preserves the immutability of the source dataset.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
import pandas as pd


@dataclass(frozen=True)
class ImageResolutionRecord:
    image_id: str
    event_id: str
    user_id: str
    resolved_amount: float
    currency: str
    evidence_description: str
    confidence: float
    alternative_displayed_amount: Optional[float] = None
    ambiguity_note: Optional[str] = None


# Ground-truth verified resolution registry for the 16 missing event amounts
VERIFIED_IMAGE_RESOLUTIONS: Dict[str, ImageResolutionRecord] = {
    "image_01": ImageResolutionRecord(
        image_id="image_01",
        event_id="event_253",
        user_id="user_03",
        resolved_amount=4365000.0,
        currency="IDR",
        evidence_description="Pay slip Aug-2019 Net Pay transferred to Bank Central Asia: IDR 4,365,000",
        confidence=1.0,
    ),
    "image_02": ImageResolutionRecord(
        image_id="image_02",
        event_id="event_1442",
        user_id="user_16",
        resolved_amount=100000.0,
        currency="INR",
        evidence_description="Rent Receipt #9453 dated 11/08/23: Balance Due / Outstanding rent of INR 100,000.00",
        confidence=1.0,
    ),
    "image_03": ImageResolutionRecord(
        image_id="image_03",
        event_id="event_1545",
        user_id="user_17",
        resolved_amount=41272.0,
        currency="INR",
        evidence_description="Riddhi Siddhi Nuts & Spices Bill of Supply #1125000158: Net Amount / Cash Paid INR 41,272.00",
        confidence=1.0,
    ),
    "image_04": ImageResolutionRecord(
        image_id="image_04",
        event_id="event_1700",
        user_id="user_19",
        resolved_amount=2854.0,
        currency="INR",
        evidence_description="Delivered grocery order receipt: Item Bill of INR 2,854.00 with free delivery",
        confidence=1.0,
    ),
    "image_05": ImageResolutionRecord(
        image_id="image_05",
        event_id="event_1786",
        user_id="user_20",
        resolved_amount=704.05,
        currency="INR",
        evidence_description="Airtel Thanks for Business telecom bill due 06-Feb-2026: Total INR 704.05",
        confidence=1.0,
    ),
    "image_06": ImageResolutionRecord(
        image_id="image_06",
        event_id="event_3051",
        user_id="user_33",
        resolved_amount=1995.0,
        currency="INR",
        evidence_description="Blink Commerce grocery tax invoice: Total INR 1,995.00 (One Thousand Nine Hundred Ninety-Five Only)",
        confidence=1.0,
    ),
    "image_07": ImageResolutionRecord(
        image_id="image_07",
        event_id="event_3231",
        user_id="user_35",
        resolved_amount=8528.10,
        currency="INR",
        evidence_description="Nagarjuna restaurant tax invoice: Total with GST of INR 8,528.10 (cash rounded 8,528.00)",
        confidence=0.95,
        alternative_displayed_amount=8528.00,
        ambiguity_note="Exact tax invoice total including GST is INR 8,528.10; cash grand total is displayed rounded to INR 8,528.00. Downstream engine uses exact invoice total 8528.10.",
    ),
    "image_08": ImageResolutionRecord(
        image_id="image_08",
        event_id="event_4535",
        user_id="user_48",
        resolved_amount=15339.0,
        currency="INR",
        evidence_description="Property maintenance receipt (Inv No 6455): Total Amount Received INR 15,339.00 (confirmed by message_35)",
        confidence=1.0,
    ),
    "image_09": ImageResolutionRecord(
        image_id="image_09",
        event_id="event_5170",
        user_id="user_55",
        resolved_amount=723.0,
        currency="INR",
        evidence_description="Water bill receipt (Inv No 6320) dated 07-06-2026: Total Amount Received INR 723.00",
        confidence=1.0,
    ),
    "image_10": ImageResolutionRecord(
        image_id="image_10",
        event_id="event_6033",
        user_id="user_64",
        resolved_amount=79679.26,
        currency="INR",
        evidence_description="Large grocery tax invoice dated 03-Jun-2024: Balance Due INR 79,679.26",
        confidence=1.0,
    ),
    "image_11": ImageResolutionRecord(
        image_id="image_11",
        event_id="event_6859",
        user_id="user_73",
        resolved_amount=3650.0,
        currency="INR",
        evidence_description="Jeevan Hospital Provisional Bill: Total Bill Amount / Amount Payable INR 3,650.00",
        confidence=1.0,
    ),
    "image_12": ImageResolutionRecord(
        image_id="image_12",
        event_id="event_7307",
        user_id="user_78",
        resolved_amount=33.50,
        currency="USD",
        evidence_description="CityCab taxi receipt: Ride Distance ($28.50) + Airport Surcharge ($5.00) = Total USD 33.50",
        confidence=1.0,
    ),
    "image_13": ImageResolutionRecord(
        image_id="image_13",
        event_id="event_7941",
        user_id="user_84",
        resolved_amount=2298.0,
        currency="INR",
        evidence_description="BuyBox tote bag order: Total paid INR 2,298.00 (confirmed by message_64)",
        confidence=1.0,
    ),
    "image_14": ImageResolutionRecord(
        image_id="image_14",
        event_id="event_9421",
        user_id="user_101",
        resolved_amount=4543.0,
        currency="INR",
        evidence_description="Handwritten pharmacy bill dated 02-Nov-2025: Itemized total of INR 4,543.00",
        confidence=1.0,
    ),
    "image_15": ImageResolutionRecord(
        image_id="image_15",
        event_id="event_9806",
        user_id="user_105",
        resolved_amount=9968.0,
        currency="INR",
        evidence_description="IndiGo air ticket tax invoice: Air charges (9,580.00) + Airport charges (388.00) = Total INR 9,968.00",
        confidence=1.0,
    ),
    "image_16": ImageResolutionRecord(
        image_id="image_16",
        event_id="event_10521",
        user_id="user_113",
        resolved_amount=393.22,
        currency="INR",
        evidence_description="EV charging station invoice: Energy (333.24) + GST 18% (59.98) = Total INR 393.22 (confirmed by message_86)",
        confidence=1.0,
    ),
}


class EnrichedEventsManager:
    """Manages creation and validation of enriched financial events."""

    @staticmethod
    def get_enriched_financial_events(
        raw_events: pd.DataFrame, 
        images_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Produces a derived, enriched copy of financial_events with all 16 null amounts
        recovered from the verified image resolutions.
        Does not mutate the input raw_events DataFrame.
        """
        enriched = raw_events.copy(deep=True)

        # Retain original amount column for complete audit trail
        enriched["original_amount"] = enriched["amount"]
        enriched["is_amount_resolved_from_image"] = False
        enriched["resolution_source"] = None
        enriched["resolution_evidence"] = None
        enriched["resolution_confidence"] = 0.0

        # Build lookup map: event_id -> resolution record
        event_to_res: Dict[str, ImageResolutionRecord] = {
            rec.event_id: rec for rec in VERIFIED_IMAGE_RESOLUTIONS.values()
        }

        # Apply resolutions
        for idx, row in enriched.iterrows():
            evt_id = row["event_id"]
            if evt_id in event_to_res:
                rec = event_to_res[evt_id]
                # Ensure the currency in the event record matches the resolution currency
                assert row["currency"] == rec.currency, (
                    f"Currency mismatch for {evt_id}: event={row['currency']}, rec={rec.currency}"
                )
                enriched.at[idx, "amount"] = rec.resolved_amount
                enriched.at[idx, "is_amount_resolved_from_image"] = True
                enriched.at[idx, "resolution_source"] = rec.image_id
                enriched.at[idx, "resolution_evidence"] = rec.evidence_description
                enriched.at[idx, "resolution_confidence"] = rec.confidence

        return enriched


def print_audit_table():
    """Prints formatted Step 2G manual audit table."""
    print("=" * 120)
    print(f"{'image':<10} | {'event':<12} | {'extracted amount':>16} | {'currency':<8} | {'confidence':<10} | {'evidence'}")
    print("-" * 120)
    for img_id in sorted(VERIFIED_IMAGE_RESOLUTIONS.keys()):
        rec = VERIFIED_IMAGE_RESOLUTIONS[img_id]
        print(f"{rec.image_id:<10} | {rec.event_id:<12} | {rec.resolved_amount:>16.2f} | {rec.currency:<8} | {rec.confidence:<10.1f} | {rec.evidence_description}")
    print("=" * 120)


if __name__ == "__main__":
    print_audit_table()
