from django.contrib import admin

from .models import Sandbox


@admin.register(Sandbox)
class SandboxAdmin(admin.ModelAdmin):
    """Read-only: sandboxes are made by "Try the demo" and deleted by expire_sandboxes."""

    list_display = ["name", "created_at", "expires_at", "client_ip", "user"]
    search_fields = ["name", "client_ip"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
