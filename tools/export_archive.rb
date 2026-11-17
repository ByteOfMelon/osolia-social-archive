# Run inside the osolia-social web container:
#   bin/rails runner /mastodon/public/system/export_archive.rb
# Writes /mastodon/public/system/archive-export.json using Mastodon's own REST serializers,
# so content HTML, mentions, emojis, polls and media match what the live site showed.
require 'json'

def ser(obj, serializer)
  ActiveModelSerializers::SerializableResource.new(obj, serializer: serializer, scope: nil, scope_name: :current_user).as_json
rescue => e
  warn "serialize #{obj.class}##{obj.try(:id)} failed: #{e.class}: #{e.message}"
  nil
end

local = Account.local.where(suspended_at: nil)
local = local.without_instance_actor if local.respond_to?(:without_instance_actor)

posters = Status.unscoped.where(account_id: local.select(:id), deleted_at: nil).distinct.pluck(:account_id)
real    = local.joins(:user).where.not(users: { confirmed_at: nil }).pluck(:id)
keep_ids = (posters + real).uniq
accounts = Account.where(id: keep_ids).order(:id).to_a

out = {
  exported_at: Time.now.utc.iso8601,
  instance: {
    domain: Rails.configuration.x.local_domain,
    title: (Setting.site_title rescue nil),
    short_description: (Setting.site_short_description rescue nil),
    extended_description: (Setting.site_extended_description rescue nil),
    contact_username: (Setting.site_contact_username rescue nil),
  },
  custom_emojis: CustomEmoji.local.map { |e| ser(e, REST::CustomEmojiSerializer) }.compact,
  accounts: [],
  statuses: [],
  skipped: { unconfirmed_no_posts: local.count - accounts.size },
}

accounts.each do |a|
  j = ser(a, REST::AccountSerializer) or next
  j[:noindex] = (a.user&.setting_noindex rescue false)
  j[:pinned_status_ids] = a.pinned_statuses.pluck(:id).map(&:to_s) rescue []
  j[:user_role] = (a.user&.role&.name rescue nil)
  out[:accounts] << j
end

Status.unscoped.where(account_id: accounts.map(&:id), visibility: [:public, :unlisted], deleted_at: nil)
      .find_each do |s|
  j = ser(s, REST::StatusSerializer) or next
  # source text too, in case the fork renders markdown differently later
  j[:source_text] = s.text
  j[:content_type] = s.try(:content_type)
  out[:statuses] << j
end

path = Rails.root.join('public', 'system', 'archive-export.json')
File.write(path, JSON.pretty_generate(out))
puts "accounts=#{out[:accounts].size} statuses=#{out[:statuses].size} emojis=#{out[:custom_emojis].size} skipped=#{out[:skipped][:unconfirmed_no_posts]} -> #{path}"
