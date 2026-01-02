$(document).ready(function() {
    let currentItemId = null;
    
    // Restock button functionality
    $('.restock-btn').on('click', function() {
        currentItemId = $(this).data('id');
        const itemName = $(this).data('name');
        
        $('#restockItemName').text(itemName);
        $('#restockQuantity').val(1);
        $('#restockModal').modal('show');
    });
    
    // Confirm restock
    $('#confirmRestock').on('click', function() {
        if (currentItemId) {
            const newQuantity = $('#restockQuantity').val();
            
            $.post('/update/' + currentItemId + '/', {
                'quantity': newQuantity,
                'csrfmiddlewaretoken': $('[name=csrfmiddlewaretoken]').val()
            })
            .done(function() {
                showNotification('Stock updated successfully!', 'success');
                setTimeout(() => location.reload(), 1000);
            })
            .fail(function() {
                showNotification('Error updating stock. Please try again.', 'error');
            });
        }
        
        $('#restockModal').modal('hide');
    });
    
    // Edit functionality
    $('.editable').on('click', function() {
        const cell = $(this);
        const field = cell.data('field');
        const itemId = cell.data('id');
        const currentValue = cell.text().replace('£', '').trim();
        
        if (cell.hasClass('editing')) return;
        
        cell.addClass('editing');
        const input = $('<input type="text" class="form-control form-control-sm">');
        input.val(currentValue);
        cell.html(input);
        input.focus();
        
        function saveEdit() {
            const newValue = input.val();
            
            $.post('/update/' + itemId + '/', {
                [field]: newValue,
                'csrfmiddlewaretoken': $('[name=csrfmiddlewaretoken]').val()
            })
            .done(function() {
                if (field === 'cost') {
                    cell.html('£' + parseFloat(newValue || 0).toFixed(2));
                } else {
                    cell.html(newValue);
                }
                cell.removeClass('editing');
                showNotification('Item updated successfully!', 'success');
                
                // If quantity was updated, check if page reload is needed
                if (field === 'quantity') {
                    setTimeout(() => location.reload(), 1000);
                }
            })
            .fail(function() {
                cell.html(currentValue);
                cell.removeClass('editing');
                showNotification('Error updating item. Please try again.', 'error');
            });
        }
        
        input.on('blur', saveEdit);
        input.on('keypress', function(e) {
            if (e.which === 13) {
                saveEdit();
            }
        });
    });
    
    // Notification system
    function showNotification(message, type) {
        const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
        const notification = `
            <div class="alert ${alertClass} alert-dismissible fade show position-fixed" 
                 style="top: 20px; right: 20px; z-index: 9999; min-width: 300px;">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
        
        $('body').append(notification);
        
        // Auto-dismiss after 3 seconds
        setTimeout(() => {
            $('.alert').alert('close');
        }, 3000);
    }
    
    // Table toggle functionality
    $('.table-toggle').on('click', function() {
        const targetTable = $(this).data('target');
        const $table = $(targetTable);
        const $icon = $(this).find('i');
        
        $table.collapse('toggle');
        
        $table.on('shown.bs.collapse', function() {
            $icon.removeClass('bi-chevron-down').addClass('bi-chevron-up');
        });
        
        $table.on('hidden.bs.collapse', function() {
            $icon.removeClass('bi-chevron-up').addClass('bi-chevron-down');
        });
    });
});

// Track pending tracking type changes
const pendingChanges = new Map();

// Track when a tracking type dropdown changes
function trackChange(selectElement) {
    const itemId = selectElement.getAttribute('data-id');
    const originalValue = selectElement.getAttribute('data-original');
    const newValue = selectElement.value;
    
    if (originalValue !== newValue) {
        // Mark as changed
        pendingChanges.set(itemId, newValue);
        selectElement.style.borderColor = '#ffc107';
        selectElement.style.borderWidth = '2px';
    } else {
        // Revert to original
        pendingChanges.delete(itemId);
        selectElement.style.borderColor = '';
        selectElement.style.borderWidth = '';
    }
    
    // Update button visibility and count
    updateSaveButton();
}

// Update the save button visibility and count
function updateSaveButton() {
    const btn = document.getElementById('updateItemsBtn');
    const countBadge = document.getElementById('changesCount');
    
    if (pendingChanges.size > 0) {
        btn.style.display = 'inline-block';
        countBadge.textContent = pendingChanges.size;
    } else {
        btn.style.display = 'none';
    }
}

// Save all tracking type changes
function saveAllTrackingChanges() {
    if (pendingChanges.size === 0) return;
    
    const btn = document.getElementById('updateItemsBtn');
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    
    // Disable button and show loading state
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Updating...';
    
    // Create array of promises for all updates
    const updatePromises = Array.from(pendingChanges.entries()).map(([itemId, trackingType]) => {
        return fetch(`/update/${itemId}/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': csrfToken
            },
            body: `tracking_type=${encodeURIComponent(trackingType)}&csrfmiddlewaretoken=${encodeURIComponent(csrfToken)}`
        });
    });
    
    // Wait for all updates to complete
    Promise.all(updatePromises)
        .then(responses => {
            const allSuccessful = responses.every(r => r.ok);
            
            if (allSuccessful) {
                // Show success notification
                const alertDiv = document.createElement('div');
                alertDiv.className = 'alert alert-success alert-dismissible fade show position-fixed';
                alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
                alertDiv.innerHTML = `
                    <i class="bi bi-check-circle"></i> ${pendingChanges.size} item(s) updated successfully! Reloading...
                    <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
                `;
                document.body.appendChild(alertDiv);
                
                // Reload page to reflect changes
                setTimeout(() => location.reload(), 1000);
            } else {
                throw new Error('Some updates failed');
            }
        })
        .catch(error => {
            // Show error notification
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert alert-danger alert-dismissible fade show position-fixed';
            alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
            alertDiv.innerHTML = `
                <i class="bi bi-exclamation-triangle"></i> Error updating items. Please try again.
                <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
            `;
            document.body.appendChild(alertDiv);
            
            // Re-enable button
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-save"></i> Update Items <span id="changesCount" class="badge badge-light">' + pendingChanges.size + '</span>';
        });
}

// Global function for tracking type updates (kept for compatibility)
function updateTrackingType(selectElement) {
    const itemId = selectElement.getAttribute('data-id');
    const newTrackingType = selectElement.value;
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    const originalValue = selectElement.getAttribute('data-original') || selectElement.value;
    
    // Store original value for rollback
    selectElement.setAttribute('data-original', originalValue);
    
    // Disable select and show loading state
    selectElement.disabled = true;
    selectElement.style.opacity = '0.6';
    
    fetch(`/update/${itemId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': csrfToken
        },
        body: `tracking_type=${encodeURIComponent(newTrackingType)}&csrfmiddlewaretoken=${encodeURIComponent(csrfToken)}`
    })
    .then(response => {
        if (response.ok) {
            // Show success notification
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert alert-success alert-dismissible fade show position-fixed';
            alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
            alertDiv.innerHTML = `
                <i class="bi bi-check-circle"></i> Tracking type updated! Reloading...
                <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
            `;
            document.body.appendChild(alertDiv);
            
            // Update the original value
            selectElement.setAttribute('data-original', newTrackingType);
            
            // Reload page to reflect changes in tabs
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Update failed');
        }
    })
    .catch(error => {
        // Show error notification
        const alertDiv = document.createElement('div');
        alertDiv.className = 'alert alert-danger alert-dismissible fade show position-fixed';
        alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        alertDiv.innerHTML = `
            <i class="bi bi-exclamation-triangle"></i> Error updating tracking type. Please try again.
            <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
        `;
        document.body.appendChild(alertDiv);
        
        // Revert the select value
        selectElement.value = originalValue;
        selectElement.disabled = false;
        selectElement.style.opacity = '1';
    });
}