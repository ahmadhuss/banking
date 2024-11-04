import json
import requests

import frappe
from frappe import _


class BankingError(frappe.ValidationError):
	pass


class ExceptionHandler:
	"""
	Log and throw error as received from Admin app.
	"""

	def __init__(self, exception, account=None):
		self.exception = exception
		self.account = account
		self.handle_error()

	def handle_error(self):
		if not isinstance(self.exception, requests.exceptions.HTTPError):
			self.set_in_account(_("Something went wrong. Please retry in a while."))
			frappe.log_error(title=_("Banking Error"), message=frappe.get_traceback())
			raise

		response = self.exception.response
		self.handle_auth_error(response)
		self.handle_authorization_error(response)
		self.handle_txt_html_error(response)

		content = response.json().get("message", {})
		self.handle_frappe_server_error(content, response)
		self.handle_admin_error(content)

	def handle_auth_error(self, response):
		if not response.status_code == 401:
			return

		frappe.log_error(title=_("Banking Error"), message=response.content)

		message = _("Authentication error due to invalid credentials.")
		self.set_in_account(message)
		frappe.throw(
			title=_("Banking Error"),
			msg=message,
			exc=BankingError,
		)

	def handle_authorization_error(self, response):
		if not response.status_code == 403:
			return

		frappe.log_error(title=_("Banking Error"), message=response.content)

		content = response.json().get("message", {})
		message = (
			content if isinstance(content, str) else "Authorization error due to invalid access."
		)
		self.set_in_account(message)
		frappe.throw(title=_("Banking Error"), msg=_(message), exc=BankingError)

	def handle_txt_html_error(self, response):
		"""
		Handle Gateway Error, etc. that dont have a JSON response.
		"""
		if "application/json" in response.headers.get("Content-Type", ""):
			return

		frappe.log_error(title=_("Banking Error"), message=response.content)

		message = _("Something went wrong. Please retry in a while.")
		self.set_in_account(message)
		frappe.throw(
			title=_("Banking Error"),
			msg=message,
			exc=BankingError,
		)

	def handle_frappe_server_error(self, content, response):
		response_data = response.json()

		if not content and "exc_type" in response_data:
			frappe.log_error(title=_("Banking Error"), message=response.content)

			message = response_data.get("exception") or _(
				"The server has errored. Please retry in some time."
			)
			self.set_in_account(message)
			frappe.throw(title=_("Banking Error"), msg=message, exc=BankingError)

	def handle_admin_error(self, content):
		error_data = content.get("error", {}) or content.get("data", {})

		# log only loggable errors (not sensitive data)
		frappe.log_error(title=_("Banking Error"), message=json.dumps(error_data))

		# multiple errors
		errors = error_data.get("errors")
		if errors:
			error_list = [f"{err.get('location')} - {self.get_msg(err)}" for err in errors]
			message = _("Banking Action has failed due to the following error(s):")
			message += "<br><ul><li>" + "</li><li>".join(error_list) + "</li></ul>"
			self.set_in_account(message)
			frappe.throw(title=_("Banking Error"), msg=message, exc=BankingError)
		elif error_data.get("message"):
			message = self.get_msg(error_data)
			self.set_in_account(message)
			frappe.throw(title=_("Banking Error"), msg=message, exc=BankingError)

	def get_msg(self, error: dict):
		"""Add instructions to Kosma error messages."""
		msg = error.get("message")

		if error.get("code") == "CONSENT.RESOURCE_NOT_GRANTED":
			info = _("Please go to Banking Settings and click on {0}.").format(
				frappe.bold(_("Link Bank and Accounts"))
			)
			msg += " " + info

		return msg

	def set_in_account(self, error: str):
		"""Set error in Bank Account."""
		if not self.account:
			return

		frappe.db.set_value("Bank Account", self.account, "error_message", error)
		frappe.db.commit()  # seems required


@frappe.whitelist()
def handle_ui_error(error: str, session_id_short: str):
	from banking.klarna_kosma_integration.admin import Admin

	if error:  # might be empty
		error = json.loads(error)
		frappe.log_error(title=_("Banking Error"), message=error.get("message"))

	doc = frappe.get_doc("Klarna Kosma Session", session_id_short)
	session_id = doc.get_password("session_id")

	Admin().end_session(session_id, session_id_short)
