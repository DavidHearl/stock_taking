from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Ticket


@login_required
def tickets_list(request):
    """List all tickets and handle new ticket submission."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        priority = request.POST.get('priority', 'medium')
        image = request.FILES.get('image')
        
        if title and description:
            ticket = Ticket.objects.create(
                title=title,
                description=description,
                priority=priority,
                image=image,
                submitted_by=request.user,
            )
            messages.success(request, f'Ticket #{ticket.id} created successfully.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        else:
            messages.error(request, 'Title and description are required.')
    
    # Franchise users can only see their own tickets
    profile = getattr(request.user, 'profile', None)
    is_franchise = profile and profile.role and profile.role.name == 'franchise'
    if is_franchise:
        tickets = Ticket.objects.filter(submitted_by=request.user)
    else:
        tickets = Ticket.objects.all()
    
    # Filter by status – default to 'open' so the page loads with open tickets
    status_filter = request.GET.get('status', 'open')
    if status_filter and status_filter != 'all':
        tickets = tickets.filter(status=status_filter)
    
    context = {
        'tickets': tickets,
        'status_filter': status_filter,
    }
    return render(request, 'stock_take/tickets.html', context)


@login_required
def ticket_detail(request, ticket_id):
    """View a single ticket's details."""
    ticket = get_object_or_404(Ticket, id=ticket_id)
    context = {
        'ticket': ticket,
    }
    return render(request, 'stock_take/ticket_detail.html', context)


@login_required
def ticket_update_status(request, ticket_id):
    """Update a ticket's status."""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        new_status = request.POST.get('status')
        if new_status in dict(Ticket.STATUS_CHOICES):
            ticket.status = new_status
            ticket.save()
            messages.success(request, f'Ticket #{ticket.id} updated to {ticket.get_status_display()}.')
    return redirect('ticket_detail', ticket_id=ticket_id)


@login_required
def ticket_edit(request, ticket_id):
    """Edit a ticket – only the submitter or staff can edit."""
    ticket = get_object_or_404(Ticket, id=ticket_id)

    if request.user != ticket.submitted_by and not request.user.is_staff:
        messages.error(request, 'You can only edit your own tickets.')
        return redirect('ticket_detail', ticket_id=ticket_id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        priority = request.POST.get('priority', ticket.priority)
        image = request.FILES.get('image')

        if title and description:
            ticket.title = title
            ticket.description = description
            ticket.priority = priority
            if image:
                ticket.image = image
            ticket.save()
            messages.success(request, f'Ticket #{ticket.id} updated successfully.')
        else:
            messages.error(request, 'Title and description are required.')

    return redirect('ticket_detail', ticket_id=ticket_id)


@login_required
def ticket_delete(request, ticket_id):
    """Delete a ticket."""
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if request.method == 'POST':
        if request.user == ticket.submitted_by or request.user.is_staff:
            ticket_num = ticket.id
            ticket.delete()
            messages.success(request, f'Ticket #{ticket_num} deleted.')
        else:
            messages.error(request, 'You can only delete your own tickets.')
    return redirect('tickets_list')
