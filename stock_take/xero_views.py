"""
Xero OAuth2 views: connect, callback, disconnect, and status dashboard.
All API usage is read-only.
"""
import secrets
import logging

from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse

from stock_take.services import xero_api
from stock_take.models import XeroToken

logger = logging.getLogger(__name__)


@login_required
def xero_connect(request):
    """Redirect the user to Xero's authorization page."""
    # Generate a random state token to prevent CSRF
    state = secrets.token_urlsafe(32)
    request.session["xero_oauth_state"] = state
    auth_url = xero_api.get_authorization_url(state=state)
    return redirect(auth_url)


@login_required
def xero_callback(request):
    """Handle the OAuth callback from Xero after user authorizes."""
    error = request.GET.get("error")
    if error:
        messages.error(request, f"Xero authorization failed: {error}")
        return redirect("xero_status")

    code = request.GET.get("code")
    state = request.GET.get("state")

    # Validate state
    expected_state = request.session.pop("xero_oauth_state", None)
    if not state or state != expected_state:
        messages.error(request, "Invalid OAuth state. Please try connecting again.")
        return redirect("xero_status")

    if not code:
        messages.error(request, "No authorization code received from Xero.")
        return redirect("xero_status")

    try:
        # Exchange code for tokens
        token_data = xero_api.exchange_code_for_tokens(code)

        # Get connected tenants (organisations)
        tenants = xero_api.get_connected_tenants(token_data["access_token"])

        if not tenants:
            messages.error(request, "No Xero organisations found. Please connect to at least one.")
            return redirect("xero_status")

        # Use the first tenant (most apps connect to one org)
        tenant = tenants[0]
        tenant_id = tenant.get("tenantId", "")
        tenant_name = tenant.get("tenantName", "")

        # Save tokens to database
        xero_api.save_token_to_db(
            token_data,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            user=request.user,
        )

        messages.success(request, f"Successfully connected to Xero organisation: {tenant_name}")

    except Exception as e:
        logger.error(f"Xero callback error: {e}")
        messages.error(request, f"Failed to connect to Xero: {str(e)}")

    return redirect("xero_status")


@login_required
def xero_disconnect(request):
    """Disconnect from Xero by removing stored tokens."""
    if request.method == "POST":
        xero_api.disconnect()
        messages.success(request, "Disconnected from Xero successfully.")
    return redirect("xero_status")


@login_required
def xero_status(request):
    """Show the Xero connection status and basic organisation info."""
    token = XeroToken.get_active_token()
    context = {
        "connected": token is not None,
        "token": token,
        "organisation": None,
    }

    if token:
        # Try to fetch organisation info to confirm connection works
        try:
            org_data = xero_api.get_organisation()
            if org_data and "Organisations" in org_data:
                context["organisation"] = org_data["Organisations"][0]
        except Exception as e:
            logger.error(f"Failed to fetch Xero org info: {e}")
            context["org_error"] = str(e)

    return render(request, "stock_take/xero_status.html", context)


@login_required
def xero_api_test(request):
    """
    JSON endpoint to test the Xero connection by fetching organisation info.
    Useful for AJAX status checks.
    """
    access_token, tenant_id = xero_api.get_valid_access_token()
    if not access_token:
        return JsonResponse({"connected": False, "error": "No valid token"}, status=401)

    org_data = xero_api.get_organisation()
    if org_data and "Organisations" in org_data:
        org = org_data["Organisations"][0]
        return JsonResponse({
            "connected": True,
            "organisation": {
                "name": org.get("Name", ""),
                "legal_name": org.get("LegalName", ""),
                "short_code": org.get("ShortCode", ""),
                "base_currency": org.get("BaseCurrency", ""),
                "country_code": org.get("CountryCode", ""),
                "organisation_type": org.get("OrganisationType", ""),
            }
        })
    else:
        return JsonResponse({"connected": False, "error": "Could not fetch organisation"}, status=502)


@login_required
def xero_create_customer(request):
    """
    Create a new customer (contact) in Xero from a database Customer.
    Accepts POST with JSON body: {customer_id} (database PK)
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    import json
    from stock_take.models import Customer

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    customer_id = data.get("customer_id")
    if not customer_id:
        return JsonResponse({"success": False, "error": "customer_id is required"}, status=400)

    try:
        customer = Customer.objects.get(pk=customer_id)
    except Customer.DoesNotExist:
        return JsonResponse({"success": False, "error": "Customer not found"}, status=404)

    if customer.xero_id:
        return JsonResponse({
            "success": False,
            "error": f"Customer already linked to Xero (ID: {customer.xero_id})"
        }, status=400)

    # Build the contact name
    name = customer.name or f"{customer.first_name} {customer.last_name}".strip()
    if not name:
        return JsonResponse({"success": False, "error": "Customer has no name"}, status=400)

    result = xero_api.create_contact(
        name=name,
        first_name=customer.first_name,
        last_name=customer.last_name,
        email=customer.email or "",
        phone=customer.phone or "",
        address_line1=customer.address_1 or "",
        address_line2=customer.address_2 or "",
        city=customer.city or "",
        region=customer.state or "",
        postal_code=customer.postcode or "",
        country=customer.country or "",
    )

    if result and "Contacts" in result:
        contact = result["Contacts"][0]
        xero_contact_id = contact.get("ContactID", "")

        # Save the Xero ID back to the customer record
        if xero_contact_id:
            customer.xero_id = xero_contact_id
            customer.save(update_fields=["xero_id"])

        return JsonResponse({
            "success": True,
            "contact": {
                "id": xero_contact_id,
                "name": contact.get("Name", ""),
                "first_name": contact.get("FirstName", ""),
                "last_name": contact.get("LastName", ""),
                "email": contact.get("EmailAddress", ""),
                "status": contact.get("ContactStatus", ""),
            }
        })
    else:
        return JsonResponse({
            "success": False,
            "error": "Failed to create contact in Xero. Check server logs for details."
        }, status=500)


@login_required
def xero_customer_search(request):
    """
    Search customers in the local database for the Xero customer picker.
    Also does a live Xero API name search to check if the customer already
    exists in Xero (by xero_id stored locally, or by exact name match in Xero).
    Returns JSON with matching customers.
    """
    from stock_take.models import Customer
    from django.db.models import Q

    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    customers = Customer.objects.filter(
        Q(name__icontains=q) |
        Q(first_name__icontains=q) |
        Q(last_name__icontains=q) |
        Q(email__icontains=q)
    )[:15]

    # Live Xero name search — get all candidates for the query string
    xero_contacts_by_name = {}
    try:
        xero_hits = xero_api.search_contacts_by_name(q)
        for xc in xero_hits:
            xero_contacts_by_name[xc.get("Name", "").strip().lower()] = xc.get("ContactID", "")
    except Exception:
        pass  # best-effort

    results = []
    for c in customers:
        display_name = c.name or f"{c.first_name} {c.last_name}".strip()
        address_parts = [p for p in [c.address_1, c.address_2, c.city, c.state, c.postcode] if p]

        # Determine Xero ID: prefer stored value, fallback to live name match
        xero_id = c.xero_id or ""
        if not xero_id:
            xero_name_match = xero_contacts_by_name.get(display_name.strip().lower(), "")
            if xero_name_match:
                xero_id = xero_name_match
                # Persist the discovered xero_id so future checks are instant
                Customer.objects.filter(pk=c.pk).update(xero_id=xero_name_match)

        results.append({
            "id": c.pk,
            "name": display_name,
            "email": c.email or "",
            "phone": c.phone or "",
            "address": ", ".join(address_parts),
            "xero_id": xero_id,
        })

    return JsonResponse({"results": results})


@login_required
def xero_check_contact(request):
    """
    Live check: given a local customer_id, look up their exact name in Xero.
    Returns {found: bool, xero_id: str, name: str}.
    Updates the local Customer.xero_id if a match is found.
    """
    from stock_take.models import Customer

    customer_id = request.GET.get("customer_id", "").strip()
    if not customer_id:
        return JsonResponse({"error": "customer_id required"}, status=400)

    try:
        customer = Customer.objects.get(pk=int(customer_id))
    except (Customer.DoesNotExist, ValueError):
        return JsonResponse({"error": "Customer not found"}, status=404)

    display_name = customer.name or f"{customer.first_name} {customer.last_name}".strip()

    # If already stored, return immediately
    if customer.xero_id:
        return JsonResponse({"found": True, "xero_id": customer.xero_id, "name": display_name})

    # Live lookup by exact name
    try:
        xero_id = xero_api.find_contact_by_name(display_name)
    except Exception as e:
        logger.error(f"xero_check_contact error: {e}")
        return JsonResponse({"error": "Xero API unavailable"}, status=503)

    if xero_id:
        Customer.objects.filter(pk=customer.pk).update(xero_id=xero_id)
        return JsonResponse({"found": True, "xero_id": xero_id, "name": display_name})

    return JsonResponse({"found": False, "xero_id": "", "name": display_name})
